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
    start_urls: list[str],
    custom_prompt: str,
    max_items: int = 10,
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
        items = _crawl_reddit_posts(start_urls=start_urls, max_items=max_items)
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

    for item in items:
        title = item.get("title", "无标题")
        yield _to_ndjson(
            StreamEvent(
                type="post_started",
                message=f"正在处理帖子: {title[:60]}",
            )
        )

        result = process_single_reddit_item(
            item=item,
            custom_prompt=custom_prompt,
            client=client,
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
) -> PostAnalysisResult:
    title = item.get("title", "无标题")
    post_url = item.get("url") or item.get("permalink")

    try:
        real_text = extract_real_text(item.get("body", ""))

        if real_text == "REMOVED":
            return PostAnalysisResult(
                title=title,
                url=post_url,
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
                status="skipped",
                reason="纯视频贴，不消耗大模型 Token",
                textPreview="",
                imageCount=0,
                analysis=None,
            )

        combined_text = f"标题: {title}\n"
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
            status="failed",
            reason=f"Gemini 请求或帖子处理失败: {exc}",
            textPreview=None,
            imageCount=0,
            analysis=None,
        )


def _crawl_reddit_posts(start_urls: list[str], max_items: int) -> list[dict[str, Any]]:
    api_token = os.getenv("APIFY_API_TOKEN")
    if not api_token:
        raise RuntimeError("缺少环境变量 APIFY_API_TOKEN")

    apify_client = ApifyClient(api_token)
    run_input = {
        "startUrls": [{"url": url} for url in start_urls],
        "skipComments": True,
        "maxItems": max_items,
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
