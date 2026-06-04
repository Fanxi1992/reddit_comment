from typing import Any

import requests


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
