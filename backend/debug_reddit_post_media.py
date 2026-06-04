import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from app.image_utils import download_image_bytes
from app.schemas import RedditSearchResultItem


DEFAULT_POST_URL = "https://www.reddit.com/r/binance/comments/1tw0fx1/wtf_does_this_mean/"
OUTPUT_ROOT = Path(__file__).resolve().parent / "data" / "debug_media_downloads"

load_dotenv()


def main() -> None:
    args = parse_args()
    from app.comment_decision_runner import DetailEnvironmentRunner, load_adspower_profiles

    post_url = normalize_post_url(args.url) or args.url
    post_id = extract_post_id(post_url) or "unknown-post"
    output_dir = OUTPUT_ROOT / f"{post_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    profile = load_adspower_profiles()[args.profile_index - 1]
    item = RedditSearchResultItem(
        query="debug_media_capture",
        queryIntent="other",
        priority=1,
        timeRange="all",
        resultIndex=1,
        postUrl=post_url,
        postId=post_id,
        title="Debug Reddit media capture",
        subreddit=extract_subreddit(post_url) or "unknown",
        ageText="",
        votes=None,
        comments=None,
        duplicateOfQuery=None,
        matchedQueries=["debug_media_capture"],
    )

    print(f"Using AdsPower profile: env_id={profile.env_id} user_id={profile.user_id}")
    print(f"Post URL: {post_url}")
    print(f"Output dir: {output_dir}")

    with DetailEnvironmentRunner(profile, max_comments_per_post=args.max_comments) as runner:
        detail = runner.collect_detail(item)

    media_urls = [str(url).strip() for url in detail.get("media_urls") or [] if str(url).strip()]
    valid_media_urls = valid_reddit_image_urls(media_urls)
    downloaded_files = []

    for index, media_url in enumerate(valid_media_urls[: args.max_images], start=1):
        image_data = download_image_bytes(media_url)
        if not image_data:
            downloaded_files.append(
                {
                    "index": index,
                    "url": media_url,
                    "status": "failed",
                    "reason": "download_failed",
                }
            )
            continue

        suffix = suffix_from_mime_type(image_data["mime_type"])
        file_path = output_dir / f"image-{index}{suffix}"
        file_path.write_bytes(image_data["bytes_data"])
        downloaded_files.append(
            {
                "index": index,
                "url": media_url,
                "status": "success",
                "mimeType": image_data["mime_type"],
                "bytes": len(image_data["bytes_data"]),
                "file": str(file_path),
            }
        )

    report = {
        "postUrl": post_url,
        "postId": post_id,
        "detailStatus": detail.get("status"),
        "reason": detail.get("reason"),
        "finalUrl": detail.get("final_url"),
        "title": detail.get("title"),
        "subreddit": detail.get("subreddit"),
        "author": detail.get("author"),
        "postType": detail.get("post_type"),
        "bodyTextPreview": str(detail.get("body_text") or "")[:500],
        "outboundUrl": detail.get("outbound_url"),
        "allMediaUrls": media_urls,
        "validRedditImageUrls": valid_media_urls,
        "downloadedFiles": downloaded_files,
        "commentStats": {
            "reportedComments": detail.get("comments"),
            "loadedCommentCount": detail.get("loaded_comment_count"),
            "includedCommentCount": detail.get("included_comment_count"),
        },
    }
    report_path = output_dir / "detail.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nDetail summary:")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nSaved report: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug Reddit post media extraction using the existing detail collector.")
    parser.add_argument("--url", default=DEFAULT_POST_URL, help="Reddit post URL to inspect.")
    parser.add_argument("--max-images", default=3, type=int, help="Maximum valid Reddit image URLs to download.")
    parser.add_argument("--max-comments", default=5, type=int, help="Comment count limit for detail collection.")
    parser.add_argument("--profile-index", default=1, type=int, help="1-based AdsPower profile index from ADSPOWER_USER_IDS.")
    return parser.parse_args()


def extract_subreddit(url: str) -> str:
    parts = [part for part in url.split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "r" and index + 1 < len(parts):
            return f"r/{parts[index + 1]}"
    return ""


def suffix_from_mime_type(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized == "image/png":
        return ".png"
    if normalized == "image/webp":
        return ".webp"
    if normalized == "image/gif":
        return ".gif"
    return ".jpg"


def valid_reddit_image_urls(urls: list[str]) -> list[str]:
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


def normalize_post_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if not parsed.scheme:
        parsed = urlsplit(f"https://www.reddit.com{value if value.startswith('/') else '/' + value}")
    host = parsed.netloc.lower()
    if "reddit.com" not in host:
        return ""
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    match = re.match(r"^(/r/[^/]+/comments/[^/]+(?:/[^/]+)?)", path, flags=re.IGNORECASE)
    if match:
        path = match.group(1).rstrip("/")
    return urlunsplit(("https", "www.reddit.com", path, "", ""))


def extract_post_id(url: str) -> str:
    match = re.search(r"/comments/([^/?#]+)/?", url or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


if __name__ == "__main__":
    main()
