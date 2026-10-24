import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.comment_decider import (
    COMMENT_DECISION_SCHEMA,
    _build_allowed_targets,
    _build_system_prompt,
    _comment_length_guidance,
    _sanitize_comment_text,
    generate_comment_decision,
)
from app.schemas import QueryPlanGenerateRequest, RedditSearchResultItem


def _sample_product() -> QueryPlanGenerateRequest:
    return QueryPlanGenerateRequest(
        productName="Qidi Q2",
        productDescription="Enclosed coreXY 3D printer with active heated chamber for high-temp engineering filaments.",
        targetAudience="3D printing enthusiasts and functional part makers.",
        sellingPoints="Active chamber heating; PA6-CF / ABS support; Klipper firmware.",
        competitors="Bambu P1S, Creality K2, Centauri Carbon 2",
        commentRequirements="Mention engineering filaments or ABS naturally. No links.",
        forbiddenTopics="Do not discuss weapons or 3D2A.",
        desiredQueryCount=10,
    )


def _sample_search_result(post_url: str) -> RedditSearchResultItem:
    return RedditSearchResultItem(
        query="enclosed 3d printer abs",
        queryIntent="recommendation",
        priority=1,
        timeRange="week",
        resultIndex=1,
        postUrl=post_url,
        postId="abc123",
        title="Enclosed FDM Printer suggestion",
        subreddit="r/3dprinter",
        ageText="9 hr. ago",
        votes=12,
        comments=11,
        duplicateOfQuery=None,
        matchedQueries=["enclosed 3d printer abs"],
    )


def _sample_detail(post_url: str) -> dict:
    return {
        "status": "success",
        "reason": "",
        "post_url": post_url,
        "final_url": post_url,
        "title": "Enclosed FDM Printer suggestion",
        "subreddit": "r/3dprinter",
        "author": "maker_op",
        "flair": "Purchase Advice",
        "post_type": "text",
        "body_text": "Looking for an open-source friendly enclosed printer for ABS and nylon.",
        "body_length": 72,
        "media_urls": [],
        "outbound_url": "",
        "upvotes": 12,
        "comments": 11,
        "comment_tree": {
            "total_comment_count": 11,
            "loaded_comment_count": 2,
            "included_comment_count": 2,
            "comments": [
                {
                    "comment_url": f"{post_url}/comment/c101",
                    "author": "filament_nerd",
                    "is_op": False,
                    "depth": 0,
                    "score": 8,
                    "text": "Bambu is locked down if open source is a hard requirement.",
                    "replies": [
                        {
                            "comment_url": f"{post_url}/comment/c102",
                            "author": "maker_op",
                            "is_op": True,
                            "depth": 1,
                            "score": 3,
                            "text": "Yeah, I mainly want something that handles PA6-CF and ABS without warping.",
                            "replies": [],
                        }
                    ],
                }
            ],
        },
    }


class CommentDeciderUpgradeTests(unittest.TestCase):
    def test_allowed_targets_include_op_depth_and_score_metadata(self) -> None:
        post_url = "https://www.reddit.com/r/3dprinter/comments/abc123/enclosed_fdm_printer_suggestion"
        targets = _build_allowed_targets(post_url, _sample_detail(post_url))
        self.assertEqual(targets[post_url], "top-level post comment")
        self.assertIn(
            "[depth=0, score=8]",
            targets[f"{post_url}/comment/c101"],
        )
        self.assertIn(
            "[OP, depth=1, score=3]",
            targets[f"{post_url}/comment/c102"],
        )

    def test_calibrated_length_guidance_tiers(self) -> None:
        self.assertIn("6-22 English words", _comment_length_guidance("short")["instruction"])
        self.assertIn("25-50 English words", _comment_length_guidance("medium")["instruction"])
        self.assertIn("50-85 English words", _comment_length_guidance("long")["instruction"])

    def test_system_prompt_switches_between_text_and_multimodal_rules(self) -> None:
        text_prompt = _build_system_prompt(has_images=False)
        image_prompt = _build_system_prompt(has_images=True)
        self.assertIn("SOURCE FORMAT: TEXT-ONLY THREAD", text_prompt)
        self.assertIn("SOURCE FORMAT: MULTIMODAL THREAD WITH ATTACHED POST IMAGES", image_prompt)
        self.assertIn("ZERO VISUAL NARRATION (ANTI-OCR REDUNDANCY)", image_prompt)
        self.assertIn("playbook_1_conditional_partition", text_prompt)

    def test_sanitize_comment_text_removes_em_dashes_and_quotes(self) -> None:
        raw = '"Qidi Q2 if you need high-temp engineering filaments — Centauri Carbon 2 for everything else (around 15–20 mins warmup)."'
        cleaned = _sanitize_comment_text(raw)
        self.assertNotIn("—", cleaned)
        self.assertNotIn("–", cleaned)
        self.assertFalse(cleaned.startswith('"'))
        self.assertIn("15-20", cleaned)
        self.assertIn("filaments, Centauri", cleaned)

    def test_generate_comment_decision_sends_system_prompt_and_returns_strategy_reason(self) -> None:
        post_url = "https://www.reddit.com/r/3dprinter/comments/abc123/enclosed_fdm_printer_suggestion"
        nested_url = f"{post_url}/comment/c102"
        simulated_model_output = {
            "threadForensics": "All 4 gates pass. OP specifically asks about PA6-CF and ABS in c102.",
            "shouldComment": True,
            "skipReason": None,
            "selectedPlaybook": "playbook_1_conditional_partition",
            "voiceProfile": "profile_b_crisp_partitioner",
            "commentUrl": nested_url,
            "commentText": "Qidi Q2 if you are interested in higher end engineering filaments — Centauri Carbon 2 for everything else.",
        }
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(simulated_model_output)}}]
        }

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch("app.openrouter_client.requests.post", return_value=mock_response) as mock_post:
                result = generate_comment_decision(
                    product_context=_sample_product(),
                    search_result=_sample_search_result(post_url),
                    detail=_sample_detail(post_url),
                    comment_length_style="short",
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["commentUrl"], nested_url)
        self.assertNotIn("—", result["commentText"])
        self.assertEqual(result["reason"], "已生成 · 条件分流 / 务实分流 / 二级借楼")

        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["messages"][0]["role"], "system")
        self.assertEqual(sent_payload["messages"][1]["role"], "user")
        self.assertNotIn("temperature", sent_payload)
        self.assertEqual(sent_payload["reasoning"], {"effort": "medium", "exclude": True})
        self.assertEqual(
            sent_payload["response_format"]["json_schema"]["schema"],
            COMMENT_DECISION_SCHEMA,
        )

    def test_generate_comment_decision_surfaces_chinese_skip_reason(self) -> None:
        post_url = "https://www.reddit.com/r/PoliticalMeme/comments/xyz999/acmer_statement"
        simulated_model_output = {
            "threadForensics": "Gate 1 failed: ACMER here refers to American Committee for Middle East Rights, not laser engravers.",
            "shouldComment": False,
            "skipReason": "同名缩写撞词：原帖为政治话题，与激光雕刻机无关",
            "selectedPlaybook": None,
            "voiceProfile": None,
            "commentUrl": None,
            "commentText": None,
        }
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(simulated_model_output)}}]
        }

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch("app.openrouter_client.requests.post", return_value=mock_response):
                result = generate_comment_decision(
                    product_context=_sample_product(),
                    search_result=_sample_search_result(post_url),
                    detail=_sample_detail(post_url),
                    comment_length_style="medium",
                )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "同名缩写撞词：原帖为政治话题，与激光雕刻机无关")
        self.assertIsNone(result["commentUrl"])
        self.assertIsNone(result["commentText"])

    def test_openrouter_fallback_when_reasoning_param_unsupported(self) -> None:
        post_url = "https://www.reddit.com/r/3dprinter/comments/abc123/enclosed_fdm_printer_suggestion"
        simulated_model_output = {
            "threadForensics": "All gates pass.",
            "shouldComment": True,
            "skipReason": None,
            "selectedPlaybook": "playbook_2_direct_spec_counterpoint",
            "voiceProfile": "profile_a_mobile_minimalist",
            "commentUrl": post_url,
            "commentText": "qidi q2 has active chamber heating for pa6-cf and abs out of the box",
        }
        error_response = Mock()
        error_response.ok = False
        error_response.status_code = 400
        error_response.json.return_value = {"error": {"message": "Unsupported parameter: reasoning"}}

        ok_response = Mock()
        ok_response.ok = True
        ok_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(simulated_model_output)}}]
        }

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch("app.openrouter_client.requests.post", side_effect=[error_response, ok_response]) as mock_post:
                result = generate_comment_decision(
                    product_context=_sample_product(),
                    search_result=_sample_search_result(post_url),
                    detail=_sample_detail(post_url),
                    comment_length_style="short",
                )

        self.assertEqual(mock_post.call_count, 2)
        first_payload = mock_post.call_args_list[0].kwargs["json"]
        second_payload = mock_post.call_args_list[1].kwargs["json"]
        self.assertIn("reasoning", first_payload)
        self.assertNotIn("reasoning", second_payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["reason"], "已生成 · 参数直答 / 极简电报 / 一级评论")


if __name__ == "__main__":
    unittest.main()
