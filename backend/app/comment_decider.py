import json
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.reddit_analyzer import download_image_bytes
from app.schemas import QueryPlanGenerateRequest, RedditSearchResultItem


MODEL_NAME = "gemini-3.5-flash"
GEMINI_TIMEOUT_SECONDS = 2 * 60
MAX_BODY_CHARS = 5000
MAX_COMMENT_TEXT_CHARS = 900
MAX_COMMENTS_FOR_PROMPT = 30
MAX_IMAGES_FOR_GEMINI = 3
MAX_TARGET_COMMENT_PREVIEW_CHARS = 300

load_dotenv()


COMMENT_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "shouldComment": {
            "type": "boolean",
            "description": "Whether a natural, useful Reddit comment should be suggested for this post.",
        },
        "commentUrl": {
            "type": ["string", "null"],
            "description": "The exact target URL to comment on. Must be the post URL or one of the provided comment URLs.",
        },
        "commentText": {
            "type": ["string", "null"],
            "description": "The suggested English Reddit comment text, or null when shouldComment is false.",
        },
    },
    "required": ["shouldComment", "commentUrl", "commentText"],
    "additionalProperties": False,
}


class CommentDecisionError(RuntimeError):
    pass


def generate_comment_decision(
    *,
    product_context: QueryPlanGenerateRequest,
    search_result: RedditSearchResultItem,
    detail: dict[str, Any],
    comment_length_style: str = "medium",
) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise CommentDecisionError("缺少环境变量 GEMINI_API_KEY")

    allowed_targets = _build_allowed_targets(search_result.postUrl, detail)
    if not allowed_targets:
        return _skipped("没有可用的评论目标 URL")

    if _should_skip_before_gemini(detail):
        return _skipped("帖子正文和首屏评论都不足，已跳过")

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_SECONDS * 1000))
    contents: list[Any] = [_build_prompt(product_context, search_result, detail, allowed_targets, comment_length_style)]
    contents.extend(_build_image_parts(detail))

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": COMMENT_DECISION_SCHEMA,
        },
    )
    if not response.text:
        raise CommentDecisionError("Gemini 未返回评论决策")

    try:
        raw_decision = json.loads(response.text)
    except Exception as exc:
        raise CommentDecisionError(f"Gemini 返回格式无法解析: {exc}") from exc

    return _validate_decision(raw_decision, allowed_targets)


def _build_prompt(
    product_context: QueryPlanGenerateRequest,
    search_result: RedditSearchResultItem,
    detail: dict[str, Any],
    allowed_targets: dict[str, str],
    comment_length_style: str,
) -> str:
    payload = {
        "task": (
            "Decide whether this Reddit post is a good organic word-of-mouth comment opportunity. "
            "If yes, choose exactly one target URL and write one natural English Reddit comment."
        ),
        "hard_rules": [
            "Return JSON only following the schema.",
            "Default to finding a useful, natural Reddit-native angle when there is any reasonable connection to the product context.",
            "Only set shouldComment=false when the post is clearly irrelevant, unsafe, spam-sensitive, or there is no meaningful context to respond to.",
            "commentUrl must exactly match one URL from allowed_comment_targets.",
            "Use the post URL for a top-level comment, or a comment URL when replying to an existing comment.",
            "Write like a real Reddit user, not an ad.",
            "Do not overpromise, do not fabricate personal experience, and do not claim you used the product unless the user explicitly allowed that.",
            "Prefer recommendation requests, alternatives, comparisons, pain points, and problem-solution discussions.",
            "If replying to a comment, the text must directly fit that comment's context.",
        ],
        "product_context": {
            "product_name": product_context.productName,
            "product_description": product_context.productDescription,
            "target_audience": product_context.targetAudience,
            "selling_points": product_context.sellingPoints,
            "competitors": product_context.competitors,
            "comment_requirements": product_context.commentRequirements,
            "forbidden_topics": product_context.forbiddenTopics,
        },
        "source_search_result": search_result.model_dump(),
        "post_detail": _limited_detail_for_prompt(detail),
        "allowed_comment_targets": allowed_targets,
        "comment_length_guidance": _comment_length_guidance(comment_length_style),
        "output_examples": [
            {
                "shouldComment": True,
                "commentUrl": "one exact URL from allowed_comment_targets",
                "commentText": "A concise, natural English Reddit comment.",
            },
            {
                "shouldComment": False,
                "commentUrl": None,
                "commentText": None,
            },
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _comment_length_guidance(style: str) -> dict[str, str]:
    guidance = {
        "short": {
            "selected_length": "short",
            "instruction": (
                "Write a short Reddit comment: 1-2 sentences, about 25-60 English words. "
                "Keep it direct, lightweight, and natural, like a quick useful reply."
            ),
        },
        "medium": {
            "selected_length": "medium",
            "instruction": (
                "Write a medium-length Reddit comment: 2-4 sentences, about 60-120 English words. "
                "Include a little reasoning, comparison, or context while staying conversational."
            ),
        },
        "long": {
            "selected_length": "long",
            "instruction": (
                "Write a longer Reddit comment: 4-7 sentences, about 120-220 English words. "
                "Use this space for nuanced comparisons, caveats, or practical reasoning, but do not sound like marketing copy."
            ),
        },
    }
    return guidance.get(style, guidance["medium"])


def _limited_detail_for_prompt(detail: dict[str, Any]) -> dict[str, Any]:
    limited = dict(detail)
    body_text = str(limited.get("body_text") or "")
    limited["body_text"] = body_text[:MAX_BODY_CHARS]
    limited["comment_tree"] = _limit_comment_tree(limited.get("comment_tree") or {})
    return limited


def _limit_comment_tree(comment_tree: dict[str, Any]) -> dict[str, Any]:
    budget = {"remaining": MAX_COMMENTS_FOR_PROMPT}

    def convert(node: dict[str, Any]) -> dict[str, Any] | None:
        if budget["remaining"] <= 0:
            return None
        budget["remaining"] -= 1
        text = re.sub(r"\s+", " ", str(node.get("text") or "")).strip()
        payload = {
            "comment_url": node.get("comment_url") or "",
            "author": node.get("author") or "unknown",
            "is_op": bool(node.get("is_op")),
            "depth": node.get("depth") or 0,
            "score": node.get("score"),
            "text": text[:MAX_COMMENT_TEXT_CHARS],
            "replies": [],
        }
        for reply in node.get("replies") or []:
            converted = convert(reply)
            if converted:
                payload["replies"].append(converted)
        return payload

    comments = []
    for comment in comment_tree.get("comments") or []:
        converted = convert(comment)
        if converted:
            comments.append(converted)
    return {
        "total_comment_count": comment_tree.get("total_comment_count") or 0,
        "loaded_comment_count": comment_tree.get("loaded_comment_count") or 0,
        "included_comment_count": MAX_COMMENTS_FOR_PROMPT - budget["remaining"],
        "comments": comments,
    }


def _build_image_parts(detail: dict[str, Any]) -> list[Any]:
    post_type = str(detail.get("post_type") or "").lower()
    if post_type not in {"image", "gallery"}:
        return []

    image_parts = []
    for url in _valid_image_urls(detail.get("media_urls") or [])[:MAX_IMAGES_FOR_GEMINI]:
        image_data = download_image_bytes(url)
        if not image_data:
            continue
        image_parts.append(
            types.Part.from_bytes(
                data=image_data["bytes_data"],
                mime_type=image_data["mime_type"],
            )
        )
    return image_parts


def _valid_image_urls(urls: list[str]) -> list[str]:
    output = []
    for url in urls:
        value = str(url or "").replace("&amp;", "&").strip()
        lowered = value.lower()
        if not value:
            continue
        if "external-preview.redd.it" in lowered or "external-i.redd.it" in lowered:
            continue
        if lowered.startswith("https://i.redd.it/") or lowered.startswith("https://preview.redd.it/"):
            output.append(value)
    return output


def _build_allowed_targets(post_url: str, detail: dict[str, Any]) -> dict[str, str]:
    targets: dict[str, str] = {}
    normalized_post_url = _normalize_reddit_target_url(post_url) or _normalize_reddit_target_url(detail.get("post_url") or "")
    if normalized_post_url:
        targets[normalized_post_url] = "top-level post comment"

    def walk(node: dict[str, Any]) -> None:
        comment_url = _normalize_reddit_target_url(str(node.get("comment_url") or ""))
        text = re.sub(r"\s+", " ", str(node.get("text") or "")).strip()
        if comment_url and text:
            targets[comment_url] = f"reply to {node.get('author') or 'comment'}: {text[:MAX_TARGET_COMMENT_PREVIEW_CHARS]}"
        for reply in node.get("replies") or []:
            walk(reply)

    for comment in ((detail.get("comment_tree") or {}).get("comments") or []):
        walk(comment)
    return targets


def _validate_decision(raw_decision: dict[str, Any], allowed_targets: dict[str, str]) -> dict[str, Any]:
    should_comment = bool(raw_decision.get("shouldComment"))
    if not should_comment:
        return _skipped("Gemini 判断该帖子不适合自然评论")

    comment_url = _normalize_reddit_target_url(str(raw_decision.get("commentUrl") or ""))
    comment_text = str(raw_decision.get("commentText") or "").strip()
    if not comment_url or comment_url not in allowed_targets:
        return _skipped("Gemini 返回的评论目标 URL 不在可用目标列表中")
    if not comment_text:
        return _skipped("Gemini 未返回有效评论内容")
    return {
        "status": "success",
        "reason": "",
        "commentUrl": comment_url,
        "commentText": comment_text,
    }


def _should_skip_before_gemini(detail: dict[str, Any]) -> bool:
    body_text = str(detail.get("body_text") or "").strip()
    comments = (detail.get("comment_tree") or {}).get("comments") or []
    post_type = str(detail.get("post_type") or "").lower()
    if post_type == "video" and not body_text and not comments:
        return True
    return not body_text and not comments


def _skipped(reason: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "commentUrl": None,
        "commentText": None,
    }


def _normalize_reddit_target_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if not parsed.scheme:
        parsed = urlsplit(f"https://www.reddit.com{value if value.startswith('/') else '/' + value}")
    host = parsed.netloc.lower()
    if "reddit.com" not in host:
        return ""
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunsplit(("https", "www.reddit.com", path, "", ""))
