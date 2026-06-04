import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path

import requests
from dotenv import load_dotenv


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

load_dotenv()

# Keep this empty. Put the real key in backend/.env as OPENROUTER_API_KEY.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Put your local image path here, then run this script directly.
# Keep the leading r before the quote so Windows backslashes are treated safely.
IMAGE_PATH = r"4.jpg"


def main() -> None:
    args = parse_args()
    api_key = args.api_key or os.getenv("OPENROUTER_API_KEY") or OPENROUTER_API_KEY
    if not api_key:
        raise SystemExit(
            "Missing OpenRouter API key. Set OPENROUTER_API_KEY, pass --api-key, "
            "or fill OPENROUTER_API_KEY in backend/.env."
        )

    image_value = args.image or IMAGE_PATH
    if not image_value.strip():
        raise SystemExit("Missing image path. Fill IMAGE_PATH in this file or pass an image path argument.")

    image_path = resolve_image_path(image_value)
    if not image_path.is_file():
        raise SystemExit(f"Image file not found: {image_path}")

    data_url = image_file_to_data_url(image_path)
    payload = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": args.prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                        },
                    },
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 5000,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "outer_comment_generator image base64 test",
    }

    response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=args.timeout)
    print(f"HTTP {response.status_code}")
    try:
        response_payload = response.json()
    except ValueError:
        print(response.text)
        response.raise_for_status()
        return

    if not response.ok:
        print(json.dumps(response_payload, ensure_ascii=False, indent=2))
        response.raise_for_status()

    content = (
        response_payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    print("\nModel response:")
    print(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test sending a local image to OpenRouter as a base64 data URL."
    )
    parser.add_argument(
        "image",
        nargs="?",
        help="Path to a local image file, e.g. .jpg, .png, .webp, or .gif.",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="OpenRouter API key. If omitted, OPENROUTER_API_KEY env var or the file placeholder is used.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENROUTER_VISION_MODEL", "google/gemini-3.5-flash"),
        help="OpenRouter vision-capable model id.",
    )
    parser.add_argument(
        "--prompt",
        default="Describe the visible content of this image in 2-4 concise sentences.",
        help="Prompt to send with the image.",
    )
    parser.add_argument(
        "--timeout",
        default=120,
        type=int,
        help="HTTP timeout in seconds.",
    )
    return parser.parse_args()


def image_file_to_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    if mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise SystemExit(
            f"Unsupported or unknown image MIME type: {mime_type}. "
            "Use a PNG, JPEG, WebP, or GIF image."
        )

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def resolve_image_path(value: str) -> Path:
    raw_path = Path(value).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (Path(__file__).resolve().parent / raw_path).resolve()


if __name__ == "__main__":
    main()
