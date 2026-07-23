import base64
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from app.image_utils import download_image_bytes
from app.openrouter_client import chat_json_with_images, get_openrouter_comment_model
from app.schemas import QueryPlanGenerateRequest, RedditSearchResultItem


OPENROUTER_TIMEOUT_SECONDS = 2 * 60
MAX_BODY_CHARS = 5000
MAX_COMMENT_TEXT_CHARS = 900
MAX_COMMENTS_FOR_PROMPT = 30
MAX_IMAGES_FOR_OPENROUTER = 3
MAX_TARGET_COMMENT_PREVIEW_CHARS = 300
SUPPORTED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

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
    allowed_targets = _build_allowed_targets(search_result.postUrl, detail)
    if not allowed_targets:
        return _skipped("没有可用的评论目标 URL")

    if _should_skip_before_model(detail):
        return _skipped("帖子正文和首屏评论都不足，已跳过")

    raw_decision = chat_json_with_images(
        model=get_openrouter_comment_model(),
        prompt=_build_prompt(product_context, search_result, detail, allowed_targets, comment_length_style),
        image_data_urls=build_image_data_urls(detail),
        schema_name="comment_decision",
        schema=COMMENT_DECISION_SCHEMA,
        timeout_seconds=OPENROUTER_TIMEOUT_SECONDS,
    )

    try:
        return _validate_decision(raw_decision, allowed_targets)
    except Exception as exc:
        raise CommentDecisionError(f"OpenRouter 评论决策格式无法解析: {exc}") from exc


def _build_prompt(
    product_context: QueryPlanGenerateRequest,
    search_result: RedditSearchResultItem,
    detail: dict[str, Any],
    allowed_targets: dict[str, str],
    comment_length_style: str,
) -> str:
    payload = {
        "task": (
            "Analyze the post and its comment section to match the community's tone (humor, sarcasm, slang, or deep-dive sharing). "
            "Decide if you can drop a useful, witty, or casual comment that naturally drops the product as a recommendation, alternative, or cool discovery. "
            "If yes, pick exactly one target URL and write a highly authentic, Reddit-native comment that sounds like a real person shooting the breeze."
        ),
"hard_rules": [
            "Return JSON only following the schema.",
            "Default to finding a natural angle if there's any hook. Only set shouldComment=false if the post is dead irrelevant, hostile, or pure spam.",
            "commentUrl must exactly match one URL from allowed_comment_targets.",
            "CRITICAL: Match the vibe of the existing comments. If the sub is full of cynical humor, be cynical/funny. If it's a tech sub, blend a casual joke with a quick piece of value/insight.",
            "Zero marketing fluff. Avoid cringe phrases like 'Look no further', 'Game changer', 'Highly recommend', or 'You should check out'. Talk like a regular redditor, not an employee.",
            "Adopt a realistic persona. You CAN mention personal anecdotes, hypothetical workflows, or past experiences ('I used to...', 'Reminds me of when I tried...', 'Lately I've been doing X') to make the response grounded and believable. Prioritize natural delivery over rigid truthfulness.",
            "Deliver value or a chuckle first. Use witty banter, self-deprecation, clever insights, or practical tips. The product mention should feel like an organic 'by the way' or 'worth trying' afterthought, not a pitch.",
            "If replying to a comment, match its specific context and energy perfectly (e.g., call out a shared pain point, riff on their joke, or piggyback on their advice).",
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
        "post_detail": limited_detail_for_prompt(detail),
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
                "Write a short Reddit comment: 1 sentence, about 15-35 English words. "
                "Keep it direct, lightweight, and natural, like a quick useful reply."
            ),
        },
        "medium": {
            "selected_length": "medium",
            "instruction": (
                "Write a medium-length Reddit comment: 1-2 sentences, about 40-60 English words. "
                "Include a little reasoning, comparison, or context while staying conversational."
            ),
        },
        "long": {
            "selected_length": "long",
            "instruction": (
                "Write a longer Reddit comment: 2-4 sentences, about 65-100 English words. "
                "Use this space for nuanced comparisons, caveats, or practical reasoning, but do not sound like marketing copy."
            ),
        },
    }
    return guidance.get(style, guidance["medium"])


def limited_detail_for_prompt(detail: dict[str, Any]) -> dict[str, Any]:
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


def build_image_data_urls(detail: dict[str, Any]) -> list[str]:
    image_data_urls = []
    for url in _valid_image_urls(detail.get("media_urls") or [])[:MAX_IMAGES_FOR_OPENROUTER]:
        image_data = download_image_bytes(url)
        if not image_data:
            continue
        data_url = _image_bytes_to_data_url(image_data["bytes_data"], image_data["mime_type"])
        if data_url:
            image_data_urls.append(data_url)
    return image_data_urls


def _image_bytes_to_data_url(bytes_data: bytes, mime_type: str) -> str:
    normalized_mime_type = (mime_type or "image/jpeg").split(";", 1)[0].strip().lower()
    if normalized_mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        return ""
    encoded = base64.b64encode(bytes_data).decode("ascii")
    return f"data:{normalized_mime_type};base64,{encoded}"


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
        return _skipped("OpenRouter 判断该帖子不适合自然评论")

    comment_url = _normalize_reddit_target_url(str(raw_decision.get("commentUrl") or ""))
    comment_text = str(raw_decision.get("commentText") or "").strip()
    if not comment_url or comment_url not in allowed_targets:
        return _skipped("OpenRouter 返回的评论目标 URL 不在可用目标列表中")
    if not comment_text:
        return _skipped("OpenRouter 未返回有效评论内容")
    return {
        "status": "success",
        "reason": "",
        "commentUrl": comment_url,
        "commentText": comment_text,
    }


def _should_skip_before_model(detail: dict[str, Any]) -> bool:
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
