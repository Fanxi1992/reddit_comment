import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.schemas import (
    MAX_WARMUP_COMMENT_COUNT,
    MAX_WARMUP_POST_COUNT,
    WarmupCommentRequest,
    WarmupCommentStreamEvent,
)


class WarmupCommentContractTests(unittest.TestCase):
    def test_comment_count_accepts_upper_limit(self) -> None:
        payload = WarmupCommentRequest(
            postUrls=["https://www.reddit.com/r/test/comments/abc123/example/"],
            customPrompt="Generate varied top-level comments.",
            commentsPerPost=MAX_WARMUP_COMMENT_COUNT,
        )

        self.assertEqual(payload.commentsPerPost, 40)

    def test_comment_count_rejects_values_over_upper_limit(self) -> None:
        with self.assertRaises(ValidationError):
            WarmupCommentRequest(
                postUrls=["https://www.reddit.com/r/test/comments/abc123/example/"],
                customPrompt="Generate varied top-level comments.",
                commentsPerPost=MAX_WARMUP_COMMENT_COUNT + 1,
            )

    def test_request_rejects_too_many_posts(self) -> None:
        with self.assertRaises(ValidationError):
            WarmupCommentRequest(
                postUrls=[
                    f"https://www.reddit.com/r/test/comments/post{index}/example/"
                    for index in range(MAX_WARMUP_POST_COUNT + 1)
                ],
                customPrompt="Generate varied top-level comments.",
                commentsPerPost=10,
            )

    def test_request_rejects_non_post_and_duplicate_urls(self) -> None:
        for post_urls in [
            ["https://www.reddit.com/r/test/"],
            [
                "https://www.reddit.com/r/test/comments/abc123/example/",
                "https://www.reddit.com/r/test/comments/abc123/example/",
            ],
        ]:
            with self.subTest(post_urls=post_urls), self.assertRaises(ValidationError):
                WarmupCommentRequest(
                    postUrls=post_urls,
                    customPrompt="Generate varied top-level comments.",
                    commentsPerPost=10,
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
