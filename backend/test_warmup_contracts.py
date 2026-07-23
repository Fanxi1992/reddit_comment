import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.schemas import (
    MAX_WARMUP_COMMENT_COUNT,
    MAX_WARMUP_POST_COUNT,
    WarmupCollectRequest,
    WarmupCollectedPost,
    WarmupCommentRequest,
    WarmupCommentStreamEvent,
)
from app.reddit_searcher import normalize_post_url


def make_collected_post(post_index: int = 1, post_id: str = "abc123") -> WarmupCollectedPost:
    return WarmupCollectedPost(
        postIndex=post_index,
        postUrl=f"https://www.reddit.com/r/test/comments/{post_id}/example",
        title=f"Post {post_index}",
        postType="text",
        bodyText="Example body",
        bodyLength=12,
        loadedCommentCount=1,
        includedCommentCount=1,
        commentTree={"comments": [{"text": "Existing comment", "replies": []}]},
    )


class WarmupCommentContractTests(unittest.TestCase):
    def test_short_reddit_url_is_normalized_without_query_parameters(self) -> None:
        self.assertEqual(normalize_post_url("https://redd.it/abc123?share_id=x"), "https://redd.it/abc123")

    def test_comment_count_accepts_upper_limit(self) -> None:
        payload = WarmupCommentRequest(
            posts=[make_collected_post()],
            customPrompt="Generate varied top-level comments.",
            commentsPerPost=MAX_WARMUP_COMMENT_COUNT,
        )

        self.assertEqual(payload.commentsPerPost, 40)

    def test_comment_count_rejects_values_over_upper_limit(self) -> None:
        with self.assertRaises(ValidationError):
            WarmupCommentRequest(
                posts=[make_collected_post()],
                customPrompt="Generate varied top-level comments.",
                commentsPerPost=MAX_WARMUP_COMMENT_COUNT + 1,
            )

    def test_collection_rejects_too_many_posts(self) -> None:
        with self.assertRaises(ValidationError):
            WarmupCollectRequest(
                postUrls=[
                    f"https://www.reddit.com/r/test/comments/post{index}/example/"
                    for index in range(MAX_WARMUP_POST_COUNT + 1)
                ],
            )

    def test_collection_rejects_non_post_and_duplicate_urls(self) -> None:
        for post_urls in [
            ["https://www.reddit.com/r/test/"],
            [
                "https://www.reddit.com/r/test/comments/abc123/example/",
                "https://www.reddit.com/r/test/comments/abc123/example/",
            ],
        ]:
            with self.subTest(post_urls=post_urls), self.assertRaises(ValidationError):
                WarmupCollectRequest(postUrls=post_urls)

    def test_generation_rejects_duplicate_post_indexes_and_urls(self) -> None:
        duplicate_index = make_collected_post()
        duplicate_url = make_collected_post(post_index=2)
        for posts in [
            [duplicate_index, duplicate_url],
            [make_collected_post(), make_collected_post(post_index=1, post_id="def456")],
        ]:
            with self.subTest(posts=posts), self.assertRaises(ValidationError):
                WarmupCommentRequest(
                    posts=posts,
                    customPrompt="Generate comments",
                    commentsPerPost=2,
                )

    def test_done_event_accepts_flat_comment_results(self) -> None:
        event = WarmupCommentStreamEvent.model_validate(
            {
                "type": "done",
                "summary": {
                    "totalPosts": 2,
                    "processedPosts": 2,
                    "successfulPosts": 2,
                    "failedPosts": 0,
                    "commentsPerPost": 1,
                    "generatedCommentCount": 2,
                },
                "results": [
                    {
                        "postIndex": 1,
                        "postUrl": "https://www.reddit.com/r/test/comments/abc123/example",
                        "commentIndex": 1,
                        "text": "First comment",
                    },
                    {
                        "postIndex": 2,
                        "postUrl": "https://www.reddit.com/r/test/comments/def456/example",
                        "commentIndex": 1,
                        "text": "Second comment",
                    },
                ],
            }
        )

        self.assertEqual(len(event.results or []), 2)


if __name__ == "__main__":
    unittest.main()
