import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas import WarmupCollectedPost
from app.warmup_comment_generator import (
    _build_comment_slots,
    _build_response_schema,
    _build_warmup_system_prompt,
    _sanitize_warmup_comment_text,
    generate_warmup_comments,
)


def _sample_warmup_post(with_images: bool = False) -> WarmupCollectedPost:
    post_url = "https://www.reddit.com/r/macapps/comments/xyz123/finally_cleaned_up_my_demo_recording_workflow"
    return WarmupCollectedPost(
        postIndex=1,
        postUrl=post_url,
        finalUrl=post_url,
        title="Finally cleaned up my product demo recording workflow with Tella",
        subreddit="r/macapps",
        author="indie_founder_op",
        flair="Workflow",
        postType="image" if with_images else "text",
        bodyText="Used to spend 2 hours keyframing zoom in Premiere for a 3-minute changelog video. Cut it down to 15 mins.",
        bodyLength=106,
        mediaUrls=["https://i.redd.it/sample1.png"] if with_images else [],
        outboundUrl=None,
        upvotes=24,
        totalCommentCount=3,
        loadedCommentCount=1,
        includedCommentCount=1,
        commentTree={
            "total_comment_count": 3,
            "loaded_comment_count": 1,
            "included_comment_count": 1,
            "comments": [
                {
                    "comment_url": f"{post_url}/comment/c1",
                    "author": "dev_user",
                    "is_op": False,
                    "depth": 0,
                    "score": 4,
                    "text": "Manual zoom keyframes in Premiere are soul-crushing.",
                    "replies": [],
                }
            ],
        },
    )


class WarmupCommentGeneratorUpgradeTests(unittest.TestCase):
    def test_comment_slots_enforce_power_law_lengths_and_visual_dispersal(self) -> None:
        text_slots = _build_comment_slots(10, has_images=False)
        self.assertEqual(len(text_slots), 10)
        self.assertTrue(all(slot["focus_target"] == "post_text_and_topic_only" for slot in text_slots))

        length_counts: dict[str, int] = {}
        for slot in text_slots:
            band = slot["length_band"]
            length_counts[band] = length_counts.get(band, 0) + 1
        self.assertEqual(length_counts["ultra_short"], 3)
        self.assertEqual(length_counts["short"], 4)
        self.assertEqual(length_counts["medium"], 2)
        self.assertEqual(length_counts["long"], 1)

        image_slots = _build_comment_slots(10, has_images=True)
        visual_slots = [slot for slot in image_slots if slot["focus_target"] == "visual_micro_detail_in_image"]
        text_only_slots = [slot for slot in image_slots if slot["focus_target"] == "post_text_and_topic_only"]
        self.assertEqual(len(visual_slots), 3)
        self.assertEqual(len(text_only_slots), 7)

    def test_system_prompt_contains_zero_brand_echo_and_modality_rules(self) -> None:
        text_prompt = _build_warmup_system_prompt(has_images=False)
        image_prompt = _build_warmup_system_prompt(has_images=True)
        self.assertIn("THE DEFAULT ZERO-BRAND-ECHO RULE", text_prompt)
        self.assertIn("STRICT QUOTA ISOLATION", text_prompt)
        self.assertIn("ZERO OPENING-BIGRAM COLLISIONS", text_prompt)
        self.assertIn("SOURCE FORMAT — TEXT-ONLY THREAD", text_prompt)
        self.assertIn("SOURCE FORMAT — MULTIMODAL THREAD WITH ATTACHED POST IMAGES", image_prompt)
        self.assertIn("VISUAL FOCUS DISPERSAL", image_prompt)

    def test_sanitize_warmup_comment_text_strips_numbering_dashes_and_mobile_period(self) -> None:
        raw_mobile = '1. "clean timeline layout — manual keyframes used to eat my whole afternoon."'
        cleaned_mobile = _sanitize_warmup_comment_text(raw_mobile, voice_profile="voice_mobile_lowercase")
        self.assertEqual(
            cleaned_mobile,
            "clean timeline layout, manual keyframes used to eat my whole afternoon",
        )

        raw_standard = "Comment #2: How does the export hold up on 4K 60fps timelines — any dropped frames?"
        cleaned_standard = _sanitize_warmup_comment_text(raw_standard, voice_profile="voice_curious_asker")
        self.assertEqual(
            cleaned_standard,
            "How does the export hold up on 4K 60fps timelines, any dropped frames?",
        )

    def test_generate_warmup_comments_uses_cot_schema_and_returns_clean_list(self) -> None:
        post = _sample_warmup_post(with_images=False)
        simulated_output = {
            "postForensics": {
                "exposedBrandsInPost": "Tella, Premiere",
                "plannerBrandAndAngleQuota": "Default zero-brand-echo: planner wants natural workflow discussion without repeating Tella.",
                "distinctDiscussionHooks": [
                    "2 hours vs 15 minutes editing time reduction",
                    "tedium of manual zoom keyframes in Premiere",
                    "3-minute changelog video format length",
                    "audio/mic sync during segmented recording",
                ],
            },
            "comments": [
                {
                    "slotIndex": 1,
                    "hookUsed": "2 hours vs 15 minutes editing time reduction",
                    "niche": "mobile_drive_by_banter",
                    "voiceProfile": "voice_mobile_lowercase",
                    "lengthBand": "ultra_short",
                    "text": "1. cutting a 2 hour edit down to 15 mins is huge.",
                },
                {
                    "slotIndex": 2,
                    "hookUsed": "3-minute changelog video format length",
                    "niche": "curious_op_hook",
                    "voiceProfile": "voice_curious_asker",
                    "lengthBand": "short",
                    "text": "Do you script the 3-minute changelogs word for word — or just bullet-point the clips?",
                },
                {
                    "slotIndex": 3,
                    "hookUsed": "tedium of manual zoom keyframes in Premiere",
                    "niche": "shared_pain_commiseration",
                    "voiceProfile": "voice_pragmatic_peer",
                    "lengthBand": "medium",
                    "text": "Easing speed curves on every single cursor movement in a traditional NLE is the main reason I procrastinate shipping product updates.",
                },
            ],
        }
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(simulated_output)}}]
        }

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch("app.openrouter_client.requests.post", return_value=mock_response) as mock_post:
                output = generate_warmup_comments(
                    post=post,
                    custom_prompt="围绕录屏剪辑痛点自然灌水讨论，不要重复提品牌名",
                    comment_count=3,
                )

        self.assertEqual(output.attached_image_count, 0)
        self.assertEqual(len(output.comments), 3)
        self.assertEqual(output.comments[0], "cutting a 2 hour edit down to 15 mins is huge")
        self.assertEqual(
            output.comments[1],
            "Do you script the 3-minute changelogs word for word, or just bullet-point the clips?",
        )
        self.assertNotIn("—", output.comments[1])

        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["messages"][0]["role"], "system")
        self.assertEqual(sent_payload["messages"][1]["role"], "user")
        self.assertEqual(sent_payload["temperature"], 0.85)
        self.assertEqual(sent_payload["reasoning"], {"effort": "medium", "exclude": True})
        self.assertEqual(
            sent_payload["response_format"]["json_schema"]["schema"],
            _build_response_schema(3),
        )


if __name__ == "__main__":
    unittest.main()
