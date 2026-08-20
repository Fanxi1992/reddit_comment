import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import playwright.sync_api  # noqa: F401
except ModuleNotFoundError:
    playwright_module = types.ModuleType("playwright")
    sync_api_module = types.ModuleType("playwright.sync_api")
    sync_api_module.Error = Exception
    sync_api_module.Locator = object
    sync_api_module.Page = object
    sync_api_module.TimeoutError = TimeoutError
    sync_api_module.sync_playwright = lambda: None
    sys.modules.setdefault("playwright", playwright_module)
    sys.modules.setdefault("playwright.sync_api", sync_api_module)

from app.reddit_searcher import (
    REDDIT_SEARCH_NAVIGATION_TIMEOUT_MS,
    AdsPowerSettings,
    QuerySearchResult,
    RawSearchResult,
    RedditSearchRunner,
    SearchResultSelector,
    build_reddit_search_url,
    evaluate_search_filter_reject_reason,
    is_expected_reddit_search_url,
    parse_reddit_age_hours,
    run_reddit_search_batch,
)
from app.schemas import PlannedQuery, QueryPlanGenerateRequest, RedditSearchRequest, SearchFilterCriteria


def make_item(
    index: int,
    *,
    age_text: str = "1d ago",
    votes: int | None = 100,
    comments: int | None = 30,
) -> RawSearchResult:
    return RawSearchResult(
        query="test query",
        result_index=index,
        post_url=f"https://www.reddit.com/r/test/comments/post{index}/title/",
        post_id=f"post{index}",
        title=f"Post {index}",
        subreddit="r/test",
        age_text=age_text,
        votes=votes,
        comments=comments,
        raw_text="",
    )


class RedditSearchUrlTests(unittest.TestCase):
    def test_builds_explicit_posts_relevance_url_for_every_supported_time_range(self) -> None:
        expected_urls = {
            "week": "https://www.reddit.com/search/?q=what+is+the+best+man&type=posts&sort=relevance&t=week",
            "month": "https://www.reddit.com/search/?q=what+is+the+best+man&type=posts&sort=relevance&t=month",
            "all": "https://www.reddit.com/search/?q=what+is+the+best+man&type=posts&sort=relevance&t=all",
        }

        for search_time, expected_url in expected_urls.items():
            with self.subTest(search_time=search_time):
                self.assertEqual(
                    build_reddit_search_url(
                        "https://www.reddit.com/",
                        "what is the best man",
                        search_sort="relevance",
                        search_time=search_time,
                    ),
                    expected_url,
                )

    def test_encodes_query_characters_without_turning_them_into_url_parameters(self) -> None:
        query = 'AI video & image + C++ / "tools"?'

        url = build_reddit_search_url(
            "https://www.reddit.com/existing/path?ignored=true",
            query,
            search_sort="relevance",
            search_time="month",
        )

        self.assertIn("q=AI+video+%26+image+%2B+C%2B%2B+%2F+%22tools%22%3F", url)
        self.assertTrue(
            is_expected_reddit_search_url(
                url,
                query=query,
                search_sort="relevance",
                search_time="month",
            )
        )

    def test_search_url_validation_accepts_parameter_reordering_and_tracking_parameters(self) -> None:
        url = (
            "https://reddit.com/search?sort=relevance&t=week&type=posts"
            "&q=what+is+the+best+man&tracking_id=example"
        )

        self.assertTrue(
            is_expected_reddit_search_url(
                url,
                query="what is the best man",
                search_sort="relevance",
                search_time="week",
            )
        )

    def test_search_url_validation_rejects_wrong_route_or_search_contract(self) -> None:
        invalid_urls = [
            "https://example.com/search/?q=test&type=posts&sort=relevance&t=week",
            "https://www.reddit.com/r/test/?q=test&type=posts&sort=relevance&t=week",
            "https://www.reddit.com/search/?q=other&type=posts&sort=relevance&t=week",
            "https://www.reddit.com/search/?q=test&type=all&sort=relevance&t=week",
            "https://www.reddit.com/search/?q=test&type=posts&sort=top&t=week",
            "https://www.reddit.com/search/?q=test&type=posts&sort=relevance&t=year",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(
                    is_expected_reddit_search_url(
                        url,
                        query="test",
                        search_sort="relevance",
                        search_time="week",
                    )
                )

    def test_builder_rejects_blank_query_unsupported_options_and_non_reddit_target(self) -> None:
        invalid_inputs = [
            {
                "target_url": "https://www.reddit.com",
                "query": " ",
                "search_sort": "relevance",
                "search_time": "week",
            },
            {
                "target_url": "https://www.reddit.com",
                "query": "test",
                "search_sort": "top",
                "search_time": "week",
            },
            {
                "target_url": "https://www.reddit.com",
                "query": "test",
                "search_sort": "relevance",
                "search_time": "year",
            },
            {
                "target_url": "https://example.com",
                "query": "test",
                "search_sort": "relevance",
                "search_time": "week",
            },
        ]

        for values in invalid_inputs:
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_reddit_search_url(**values)


class FakeNavigationPage:
    def __init__(self, final_url: str | None = None) -> None:
        self.url = "about:blank"
        self.final_url = final_url
        self.goto_calls: list[tuple[str, str, int]] = []

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append((url, wait_until, timeout))
        self.url = self.final_url or url


class RedditSearchNavigationTests(unittest.TestCase):
    def make_runner(self) -> RedditSearchRunner:
        return RedditSearchRunner(
            AdsPowerSettings(
                api_url="http://127.0.0.1:50325",
                api_key="test-key",
                user_id="test-user",
                target_url="https://www.reddit.com",
            )
        )

    def test_navigates_directly_to_the_explicit_search_url(self) -> None:
        runner = self.make_runner()
        page = FakeNavigationPage()

        with patch.object(runner, "_settle_page") as settle_page:
            final_url = runner._navigate_to_search_results_page(
                page,
                "what is the best man",
                search_sort="relevance",
                search_time="week",
            )

        expected_url = (
            "https://www.reddit.com/search/?q=what+is+the+best+man"
            "&type=posts&sort=relevance&t=week"
        )
        self.assertEqual(final_url, expected_url)
        self.assertEqual(
            page.goto_calls,
            [(expected_url, "domcontentloaded", REDDIT_SEARCH_NAVIGATION_TIMEOUT_MS)],
        )
        settle_page.assert_called_once_with(page)

    def test_rejects_navigation_that_does_not_land_on_the_expected_search(self) -> None:
        runner = self.make_runner()
        page = FakeNavigationPage(final_url="https://www.reddit.com/")

        with patch.object(runner, "_settle_page"), self.assertRaisesRegex(
            RuntimeError,
            "unexpected_reddit_search_url",
        ):
            runner._navigate_to_search_results_page(
                page,
                "test query",
                search_sort="relevance",
                search_time="month",
            )


class RedditSearchBatchPropagationTests(unittest.TestCase):
    def test_batch_passes_sort_and_time_range_to_the_shared_runner(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeRunner:
            def __init__(self, settings: AdsPowerSettings) -> None:
                self.settings = settings

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                return None

            def collect_query(self, query: str, **kwargs) -> QuerySearchResult:
                calls.append({"query": query, **kwargs})
                return QuerySearchResult(
                    status="no_results",
                    reason="no_results",
                    search_results_url="https://www.reddit.com/search/",
                    results=[],
                    raw_url_count=0,
                )

        payload = RedditSearchRequest(
            productContext=QueryPlanGenerateRequest(
                productName="Test product",
                productDescription="Test description",
            ),
            queries=[
                PlannedQuery(
                    query="test query",
                    intent="other",
                    reason="Regression test",
                    priority=1,
                    suggestedTimeRange="month",
                    targetUrlCount=3,
                )
            ],
            perQueryLimit=20,
            searchSort="relevance",
        )
        settings = AdsPowerSettings(
            api_url="http://127.0.0.1:50325",
            api_key="test-key",
            user_id="test-user",
        )

        with (
            patch("app.reddit_searcher.load_adspower_search_profiles", return_value=[settings]),
            patch("app.reddit_searcher.RedditSearchRunner", FakeRunner),
            patch("app.reddit_searcher._load_search_env_concurrency", return_value=1),
            patch("app.reddit_searcher._load_search_queries_per_env", return_value=1),
            patch("app.reddit_searcher._load_search_max_scan_per_query", return_value=150),
        ):
            events = list(run_reddit_search_batch(payload))

        self.assertEqual(
            calls,
            [
                {
                    "query": "test query",
                    "search_sort": "relevance",
                    "search_time": "month",
                    "target_count": 3,
                    "search_filter": None,
                    "max_scan_count": 150,
                }
            ],
        )
        self.assertEqual(events[-1]["type"], "done")


class RedditSearchFilterTests(unittest.TestCase):
    def test_parse_reddit_age_hours(self) -> None:
        self.assertAlmostEqual(parse_reddit_age_hours("30m ago") or 0, 0.5)
        self.assertEqual(parse_reddit_age_hours("17h ago"), 17)
        self.assertEqual(parse_reddit_age_hours("4d ago"), 96)
        self.assertEqual(parse_reddit_age_hours("10mo ago"), 10 * 30 * 24)
        self.assertEqual(parse_reddit_age_hours("1y ago"), 365 * 24)

    def test_filter_rejects_missing_enabled_metadata(self) -> None:
        criteria = SearchFilterCriteria(maxAgeDays=3, minVotes=50, minComments=20)
        self.assertEqual(evaluate_search_filter_reject_reason(make_item(1, age_text=""), criteria), "missing_age")
        self.assertEqual(evaluate_search_filter_reject_reason(make_item(1, votes=None), criteria), "missing_votes")
        self.assertEqual(evaluate_search_filter_reject_reason(make_item(1, comments=None), criteria), "missing_comments")

    def test_filter_rejects_items_below_thresholds(self) -> None:
        criteria = SearchFilterCriteria(maxAgeDays=3, minVotes=50, minComments=20)
        self.assertEqual(evaluate_search_filter_reject_reason(make_item(1, age_text="4d ago"), criteria), "too_old")
        self.assertEqual(evaluate_search_filter_reject_reason(make_item(1, votes=49), criteria), "low_votes")
        self.assertEqual(evaluate_search_filter_reject_reason(make_item(1, comments=19), criteria), "low_comments")
        self.assertIsNone(evaluate_search_filter_reject_reason(make_item(1), criteria))

    def test_selector_stops_after_target_qualified_count(self) -> None:
        selector = SearchResultSelector(
            target_count=2,
            search_filter=SearchFilterCriteria(maxAgeDays=3, minVotes=50, minComments=20),
            max_scan_count=150,
        )

        self.assertTrue(selector.add(make_item(1, votes=10)))
        self.assertTrue(selector.add(make_item(2)))
        self.assertTrue(selector.add(make_item(3)))
        self.assertFalse(selector.add(make_item(4)))

        outcome = selector.outcome()
        self.assertTrue(outcome.target_reached)
        self.assertEqual(outcome.scanned_result_count, 3)
        self.assertEqual(outcome.rejected_result_count, 1)
        self.assertEqual(outcome.filter_reject_counts, {"low_votes": 1})
        self.assertEqual([item.post_id for item in outcome.results], ["post2", "post3"])

    def test_selector_stops_after_scan_limit_with_partial_results(self) -> None:
        selector = SearchResultSelector(
            target_count=3,
            search_filter=SearchFilterCriteria(maxAgeDays=3, minVotes=50, minComments=20),
            max_scan_count=2,
        )

        self.assertTrue(selector.add(make_item(1, comments=1)))
        self.assertTrue(selector.add(make_item(2)))
        self.assertFalse(selector.add(make_item(3)))

        outcome = selector.outcome()
        self.assertFalse(outcome.target_reached)
        self.assertEqual(outcome.scanned_result_count, 2)
        self.assertEqual(outcome.rejected_result_count, 1)
        self.assertEqual(outcome.filter_reject_counts, {"low_comments": 1})
        self.assertEqual([item.post_id for item in outcome.results], ["post2"])


if __name__ == "__main__":
    unittest.main()
