from typing import Any

import requests


MAX_IMAGE_BYTES = 12 * 1024 * 1024


def download_image_bytes(url: str) -> dict[str, Any] | None:
    clean_url = url.replace("&amp;", "&")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        with requests.get(clean_url, headers=headers, timeout=15, stream=True) as response:
            if response.status_code != 200:
                print(f"  [!] 图片下载失败 (HTTP {response.status_code}): {clean_url}")
                return None

            try:
                declared_size = int(response.headers.get("Content-Length") or 0)
            except ValueError:
                declared_size = 0
            if declared_size > MAX_IMAGE_BYTES:
                print(f"  [!] 图片超过 {MAX_IMAGE_BYTES} 字节上限: {clean_url}")
                return None

            chunks: list[bytes] = []
            received_size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received_size += len(chunk)
                if received_size > MAX_IMAGE_BYTES:
                    print(f"  [!] 图片下载后超过 {MAX_IMAGE_BYTES} 字节上限: {clean_url}")
                    return None
                chunks.append(chunk)
            content_type = response.headers.get("Content-Type", "image/jpeg")
            return {
                "mime_type": content_type,
                "bytes_data": b"".join(chunks),
            }
    except Exception as exc:
        print(f"  [!] 图片请求异常: {exc} -> {clean_url}")
        return None
