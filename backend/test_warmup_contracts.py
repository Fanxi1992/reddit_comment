import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.schemas import MAX_WARMUP_COMMENT_COUNT, WarmupCommentRequest, WarmupCommentStreamEvent


class WarmupCommentContractTests(unittest.TestCase):
    def test_comment_count_accepts_upper_limit(self) -> None:
        payload = WarmupCommentRequest(
            postUrl="https://www.reddit.com/r/test/comments/abc123/example/",
            customPrompt="Generate varied top-level comments.",
            commentCount=MAX_WARMUP_COMMENT_COUNT,
        )

        self.assertEqual(payload.commentCount, 40)

    def test_comment_count_rejects_values_over_upper_limit(self) -> None:
        with self.assertRaises(ValidationError):
            WarmupCommentRequest(
                postUrl="https://www.reddit.com/r/test/comments/abc123/example/",
                customPrompt="Generate varied top-level comments.",
                commentCount=MAX_WARMUP_COMMENT_COUNT + 1,
            )

    def test_done_event_accepts_flat_comment_results(self) -> None:
        event = WarmupCommentStreamEvent.model_validate(
            {
                "type": "done",
                "summary": {"requestedCount": 2, "generatedCount": 2, "failedCount": 0},
                "results": [
                    {"index": 1, "text": "First comment"},
                    {"index": 2, "text": "Second comment"},
                ],
            }
        )

        self.assertEqual(len(event.results or []), 2)


if __name__ == "__main__":
    unittest.main()
