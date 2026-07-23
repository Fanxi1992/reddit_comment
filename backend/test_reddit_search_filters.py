import sys
import types
import unittest
from pathlib import Path

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

from app.reddit_searcher import RawSearchResult, SearchResultSelector, evaluate_search_filter_reject_reason, parse_reddit_age_hours
from app.schemas import SearchFilterCriteria


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
