import json
import os
from typing import Any

import requests
from dotenv import load_dotenv


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.5-flash"
DEFAULT_TIMEOUT_SECONDS = 120

load_dotenv()


class OpenRouterError(RuntimeError):
    pass


def get_openrouter_query_model() -> str:
    return os.getenv("OPENROUTER_QUERY_MODEL", "").strip() or DEFAULT_MODEL


def get_openrouter_comment_model() -> str:
    return os.getenv("OPENROUTER_COMMENT_MODEL", "").strip() or DEFAULT_MODEL


def chat_json(
    *,
    model: str,
    prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict[str, Any]:
    return _send_chat_completion(
        model=model,
        content=[{"type": "text", "text": prompt}],
        schema_name=schema_name,
        schema=schema,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def chat_json_with_images(
    *,
    model: str,
    prompt: str,
    image_data_urls: list[str],
    schema_name: str,
    schema: dict[str, Any],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": image_data_url,
            },
        }
        for image_data_url in image_data_urls
    )
    return _send_chat_completion(
        model=model,
        content=content,
        schema_name=schema_name,
        schema=schema,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _send_chat_completion(
    *,
    model: str,
    content: list[dict[str, Any]],
    schema_name: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise OpenRouterError("缺少环境变量 OPENROUTER_API_KEY")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
        "provider": {
            "require_parameters": True,
        },
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "outer_comment_generator",
    }

    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise OpenRouterError(f"OpenRouter 请求失败: {exc}") from exc

    if not response.ok:
        raise OpenRouterError(_format_error_response(response))

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise OpenRouterError("OpenRouter 返回非 JSON 响应") from exc

    content_text = _extract_content_text(response_payload)
    if not content_text:
        raise OpenRouterError("OpenRouter 未返回模型内容")

    try:
        parsed = json.loads(content_text)
    except Exception as exc:
        raise OpenRouterError(f"OpenRouter 返回内容无法解析为 JSON: {exc}; content={content_text[:500]}") from exc

    if not isinstance(parsed, dict):
        raise OpenRouterError("OpenRouter 返回 JSON 不是对象")
    return parsed


def _extract_content_text(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""


def _format_error_response(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"OpenRouter 请求失败: HTTP {response.status_code} {response.text[:500]}"
    return f"OpenRouter 请求失败: HTTP {response.status_code} {json.dumps(payload, ensure_ascii=False)[:1000]}"
