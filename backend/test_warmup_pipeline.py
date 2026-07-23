import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.schemas import WarmupCollectRequest, WarmupCommentRequest
from app.comment_decider import build_image_data_urls
from app.warmup_comment_generator import WarmupGenerationOutput, generate_warmup_comments
from app.warmup_comment_runner import run_warmup_collection_stream, run_warmup_comment_stream
from test_warmup_contracts import make_collected_post


class WarmupGeneratorTests(unittest.TestCase):
    @patch("app.comment_decider.download_image_bytes")
    def test_text_post_with_reddit_media_does_not_send_image_context(self, download_image) -> None:
        download_image.return_value = {"bytes_data": b"image", "mime_type": "image/png"}

        images = build_image_data_urls(
            {
                "post_type": "text",
                "media_urls": ["https://i.redd.it/inline-image.png"],
            }
        )

        self.assertEqual(images, [])
        download_image.assert_not_called()

    @patch("app.warmup_comment_generator.chat_json_with_images")
    @patch("app.warmup_comment_generator.build_image_data_urls")
    def test_generator_sends_images_and_requires_exact_count(self, build_images, chat) -> None:
        build_images.return_value = ["data:image/png;base64,abc"]
        chat.return_value = {"comments": [{"text": "One"}, {"text": "Two"}]}
        post = make_collected_post()
        post.postType = "image"
        post.mediaUrls = ["https://i.redd.it/example.png"]

        output = generate_warmup_comments(
            post=post,
            custom_prompt="Keep the comments casual.",
            comment_count=2,
        )

        self.assertEqual(output.comments, ["One", "Two"])
        self.assertEqual(output.attached_image_count, 1)
        self.assertEqual(chat.call_args.kwargs["image_data_urls"], ["data:image/png;base64,abc"])
        prompt = json.loads(chat.call_args.kwargs["prompt"])
        self.assertEqual(prompt["planner_instructions"], "Keep the comments casual.")
        self.assertEqual(prompt["image_context"]["attached_image_count"], 1)
        self.assertEqual(chat.call_args.kwargs["schema"]["properties"]["comments"]["minItems"], 2)


class WarmupRunnerTests(unittest.TestCase):
    @patch("app.warmup_comment_runner.run_detail_crawl")
    def test_collection_maps_detail_and_isolates_failed_posts(self, detail_crawl) -> None:
        detail_crawl.return_value = iter(
            [
                {
                    "type": "post_started",
                    "postUrl": "https://www.reddit.com/r/test/comments/abc123/example",
                },
                {
                    "type": "post_result",
                    "result": {
                        "status": "success",
                        "postUrl": "https://www.reddit.com/r/test/comments/abc123/example",
                        "title": "Fallback",
                        "subreddit": "r/test",
                        "detail": {
                            "post_url": "https://www.reddit.com/r/test/comments/abc123/example",
                            "title": "Collected title",
                            "subreddit": "r/test",
                            "post_type": "gallery",
                            "body_text": "Body",
                            "body_length": 4,
                            "media_urls": ["https://i.redd.it/a.png"],
                            "comments": 8,
                            "loaded_comment_count": 3,
                            "included_comment_count": 3,
                            "comment_tree": {"comments": []},
                        },
                    },
                },
                {
                    "type": "post_result",
                    "result": {
                        "status": "failed",
                        "postUrl": "https://www.reddit.com/r/test/comments/def456/example",
                        "reason": "blocked",
                    },
                },
            ]
        )
        payload = WarmupCollectRequest(
            postUrls=[
                "https://www.reddit.com/r/test/comments/abc123/example",
                "https://www.reddit.com/r/test/comments/def456/example",
            ]
        )

        events = list(run_warmup_collection_stream(payload))

        done = events[-1]
        self.assertEqual(done["summary"]["successfulPosts"], 1)
        self.assertEqual(done["summary"]["failedPosts"], 1)
        self.assertEqual(done["posts"][0]["mediaUrls"], ["https://i.redd.it/a.png"])
        self.assertTrue(any(event["type"] == "post_failed" for event in events))

    @patch("app.warmup_comment_runner.generate_warmup_comments")
    def test_generation_streams_comments_and_isolates_model_failure(self, generate) -> None:
        def fake_generate(*, post, custom_prompt, comment_count):
            if post.postIndex == 2:
                raise RuntimeError("model error")
            return WarmupGenerationOutput(
                comments=[f"Comment {index}" for index in range(1, comment_count + 1)],
                attached_image_count=1,
            )

        generate.side_effect = fake_generate
        payload = WarmupCommentRequest(
            posts=[make_collected_post(), make_collected_post(post_index=2, post_id="def456")],
            customPrompt="Casual comments",
            commentsPerPost=3,
        )

        events = list(run_warmup_comment_stream(payload))

        done = events[-1]
        self.assertEqual(done["summary"]["successfulPosts"], 1)
        self.assertEqual(done["summary"]["failedPosts"], 1)
        self.assertEqual(done["summary"]["generatedCommentCount"], 3)
        self.assertEqual([item["commentIndex"] for item in done["results"]], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
