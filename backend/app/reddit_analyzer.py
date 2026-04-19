import os
from collections.abc import Generator
from typing import Any

import requests
from apify_client import ApifyClient
from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.schemas import PostAnalysisResult, StreamEvent


APIFY_ACTOR_ID = "oAuCIx3ItNrs2okjQ"

load_dotenv()


def extract_real_text(body_str: str | None) -> str:
    if not body_str:
        return ""

    body_clean = body_str.strip()

    # Check whether the original Reddit post was removed or deleted.
    if body_clean in ["[removed]", "[deleted]"]:
        return "REMOVED"

    # Remove preview text injected by the Apify crawler.
    if body_clean.startswith("Images:\n\thttps://external-preview.redd.it") or body_clean.startswith(
        "Thumbnail: https://preview.redd.it"
    ):
        return ""

    return body_clean


def download_image_bytes(url: str) -> dict[str, Any] | None:
    clean_url = url.replace("&amp;", "&")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(clean_url, headers=headers, timeout=10)
        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "image/jpeg")
            return {
                "mime_type": content_type,
                "bytes_data": response.content,
            }

        print(f"  [!] 图片下载失败 (HTTP {response.status_code}): {clean_url}")
        return None
    except Exception as exc:
        print(f"  [!] 图片请求异常: {exc} -> {clean_url}")
        return None


def stream_reddit_analysis(
    posts: list[dict[str, str]],
    custom_prompt: str,
    max_items: int | None = None,
) -> Generator[str, None, None]:
    summary = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "failed": 0,
    }

    yield _to_ndjson(
        StreamEvent(
            type="crawl_started",
            message="正在启动 Apify 爬虫...",
        )
    )

    try:
        items = _crawl_reddit_posts(posts=posts, max_items=max_items)
    except Exception as exc:
        yield _to_ndjson(
            StreamEvent(
                type="error",
                message=f"Apify 爬虫启动或读取失败: {exc}",
            )
        )
        return

    summary["total"] = len(items)
    yield _to_ndjson(
        StreamEvent(
            type="crawl_completed",
            total=len(items),
            message="爬取完成，开始逐个分析帖子...",
        )
    )

    client = genai.Client()
    metadata_lookup = _build_metadata_lookup(posts)
    matched_input_urls: set[str] = set()

    for item in items:
        input_metadata = _resolve_input_metadata(item=item, lookup=metadata_lookup)
        if not input_metadata:
            print(f"  [!] Apify item 无法匹配输入 URL，跳过: {item.get('url') or item.get('permalink') or item.get('link')}")
            summary["skipped"] += 1
            continue

        matched_input_urls.add(_normalize_url(input_metadata["url"]))
        title = item.get("title", "无标题")
        yield _to_ndjson(
            StreamEvent(
                type="post_started",
                message=f"正在处理帖子: {title[:60]}",
                inputUrl=input_metadata["url"],
            )
        )

        result = process_single_reddit_item(
            item=item,
            custom_prompt=custom_prompt,
            client=client,
            input_metadata=input_metadata,
        )

        if result.status == "success":
            summary["processed"] += 1
        elif result.status == "skipped":
            summary["skipped"] += 1
        else:
            summary["failed"] += 1

        yield _to_ndjson(
            StreamEvent(
                type="post_result",
                result=result,
            )
        )

    for post in posts:
        normalized_input_url = _normalize_url(post["url"])
        if normalized_input_url in matched_input_urls:
            continue

        summary["skipped"] += 1
        yield _to_ndjson(
            StreamEvent(
                type="post_result",
                result=PostAnalysisResult(
                    title="帖子爬取出现问题",
                    url=post["url"],
                    inputUrl=post["url"],
                    communityName=_derive_community_name(post["url"]),
                    parsedCommunityName=_strip_community_prefix(_derive_community_name(post["url"])),
                    status="skipped",
                    reason="帖子爬取出现问题，已跳过",
                    textPreview="",
                    imageCount=0,
                    analysis=None,
                ),
            )
        )

    yield _to_ndjson(
        StreamEvent(
            type="done",
            message="分析完成",
            summary=summary,
        )
    )


def process_single_reddit_item(
    item: dict[str, Any],
    custom_prompt: str,
    client: genai.Client,
    input_metadata: dict[str, str] | None = None,
) -> PostAnalysisResult:
    title = item.get("title", "无标题")
    post_url = item.get("url") or item.get("permalink")
    input_url = input_metadata.get("url") if input_metadata else None
    community_name = item.get("communityName") or _derive_community_name(post_url or input_url or "")
    parsed_community_name = item.get("parsedCommunityName") or _strip_community_prefix(community_name)

    try:
        real_text = extract_real_text(item.get("body", ""))

        if real_text == "REMOVED":
            return PostAnalysisResult(
                title=title,
                url=post_url,
                inputUrl=input_url,
                communityName=community_name,
                parsedCommunityName=parsed_community_name,
                status="skipped",
                reason="帖子已被原版块移除",
                textPreview="",
                imageCount=0,
                analysis=None,
            )

        is_video = item.get("isVideo", False)
        image_urls = item.get("imageUrls", [])

        if is_video and not real_text:
            return PostAnalysisResult(
                title=title,
                url=post_url,
                inputUrl=input_url,
                communityName=community_name,
                parsedCommunityName=parsed_community_name,
                status="skipped",
                reason="纯视频贴，不消耗大模型 Token",
                textPreview="",
                imageCount=0,
                analysis=None,
            )

        combined_text = f"标题: {title}\n"
        if community_name:
            combined_text += f"所在社区: {community_name}\n"
        if real_text:
            combined_text += f"正文内容: {real_text}\n"
        combined_text += f"\n问题: {custom_prompt}"

        gemini_contents: list[Any] = [combined_text]
        image_count = 0

        if not is_video and image_urls:
            valid_image_urls = [url for url in image_urls if "external-i.redd.it" not in url]
            images_to_process = valid_image_urls[:3]

            for img_url in images_to_process:
                img_data = download_image_bytes(img_url)
                if img_data:
                    image_part = types.Part.from_bytes(
                        data=img_data["bytes_data"],
                        mime_type=img_data["mime_type"],
                    )
                    gemini_contents.append(image_part)
                    image_count += 1

        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=gemini_contents,
        )

        analysis = response.text.strip() if response.text else ""

        return PostAnalysisResult(
            title=title,
            url=post_url,
            inputUrl=input_url,
            communityName=community_name,
            parsedCommunityName=parsed_community_name,
            status="success",
            reason=None,
            textPreview=_make_text_preview(real_text),
            imageCount=image_count,
            analysis=analysis,
        )
    except Exception as exc:
        return PostAnalysisResult(
            title=title,
            url=post_url,
            inputUrl=input_url,
            communityName=community_name,
            parsedCommunityName=parsed_community_name,
            status="failed",
            reason=f"Gemini 请求或帖子处理失败: {exc}",
            textPreview=None,
            imageCount=0,
            analysis=None,
        )


def _crawl_reddit_posts(posts: list[dict[str, str]], max_items: int | None) -> list[dict[str, Any]]:
    api_token = os.getenv("APIFY_API_TOKEN")
    if not api_token:
        raise RuntimeError("缺少环境变量 APIFY_API_TOKEN")

    apify_client = ApifyClient(api_token)
    run_input = {
        "startUrls": [{"url": post["url"]} for post in posts if post.get("url")],
        "skipComments": True,
        "maxItems": max_items or len(posts),
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }

    run = apify_client.actor(APIFY_ACTOR_ID).call(run_input=run_input)
    return list(apify_client.dataset(run["defaultDatasetId"]).iterate_items())


def _to_ndjson(event: StreamEvent) -> str:
    return event.model_dump_json(exclude_none=True) + "\n"


def _make_text_preview(text: str, limit: int = 240) -> str:
    if not text:
        return ""

    compact_text = " ".join(text.split())
    if len(compact_text) <= limit:
        return compact_text
    return compact_text[:limit].rstrip() + "..."


def _build_metadata_lookup(posts: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    lookup = {}
    for post in posts:
        url = post.get("url")
        if url:
            lookup[_normalize_url(url)] = post
            post_id = _extract_reddit_post_id(url)
            if post_id:
                lookup[post_id] = post
    return lookup


def _resolve_input_metadata(
    item: dict[str, Any],
    lookup: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    candidates = [
        item.get("url"),
        item.get("permalink"),
        item.get("link"),
        item.get("parsedId"),
        item.get("id"),
    ]

    for candidate in candidates:
        if candidate:
            candidate_text = str(candidate)
            match = lookup.get(_normalize_url(candidate_text))
            if match:
                return match

            post_id = _extract_reddit_post_id(candidate_text) or _strip_reddit_kind_prefix(candidate_text)
            match = lookup.get(post_id) if post_id else None
            if match:
                return match

    return None


def _normalize_url(url: str) -> str:
    normalized = url.strip()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def _extract_reddit_post_id(value: str) -> str | None:
    normalized = value.strip()
    parts = [part for part in normalized.split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "comments" and index + 1 < len(parts):
            return parts[index + 1].lower()
    return _strip_reddit_kind_prefix(normalized)


def _strip_reddit_kind_prefix(value: str) -> str | None:
    normalized = value.strip().lower()
    if normalized.startswith("t3_"):
        return normalized[3:]
    if normalized and "/" not in normalized and "." not in normalized:
        return normalized
    return None


def _derive_community_name(url: str) -> str | None:
    parts = url.split("/")
    for index, part in enumerate(parts):
        if part.lower() == "r" and index + 1 < len(parts):
            return f"r/{parts[index + 1]}"
    return None


def _strip_community_prefix(community_name: str | None) -> str | None:
    if not community_name:
        return None
    return community_name[2:] if community_name.startswith("r/") else community_name
