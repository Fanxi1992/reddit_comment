import json
import os
from typing import Any

import requests
from dotenv import load_dotenv


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_TOKENS = 8192
VALID_REASONING_EFFORTS = {"low", "medium", "high"}

load_dotenv(override=True)


class OpenRouterError(RuntimeError):
    pass


def get_openrouter_query_model() -> str:
    return os.getenv("OPENROUTER_QUERY_MODEL", "").strip() or DEFAULT_MODEL


def get_openrouter_comment_model() -> str:
    return os.getenv("OPENROUTER_COMMENT_MODEL", "").strip() or DEFAULT_MODEL


def get_openrouter_comment_temperature() -> float | None:
    raw = os.getenv("OPENROUTER_COMMENT_TEMPERATURE", "").strip()
    if not raw or raw.lower() in {"default", "none", "auto"}:
        return None
    try:
        value = float(raw)
        return max(0.0, min(2.0, value))
    except ValueError:
        return None


def get_openrouter_warmup_temperature() -> float | None:
    raw = os.getenv("OPENROUTER_WARMUP_TEMPERATURE", "").strip()
    if not raw or raw.lower() in {"default", "none", "auto"}:
        return None
    try:
        value = float(raw)
        return max(0.0, min(2.0, value))
    except ValueError:
        return None


def get_openrouter_comment_reasoning_effort() -> str | None:
    raw = os.getenv("OPENROUTER_COMMENT_REASONING_EFFORT", "").strip().lower()
    if not raw:
        return "medium"
    if raw in {"none", "off", "false", "0", "disable", "disabled"}:
        return None
    if raw in VALID_REASONING_EFFORTS:
        return raw
    return "medium"


def chat_json(
    *,
    model: str,
    prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    system_prompt: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    return _send_chat_completion(
        model=model,
        system_prompt=system_prompt,
        content=[{"type": "text", "text": prompt}],
        schema_name=schema_name,
        schema=schema,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )


def chat_json_with_images(
    *,
    model: str,
    prompt: str,
    image_data_urls: list[str],
    schema_name: str,
    schema: dict[str, Any],
    system_prompt: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
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
        system_prompt=system_prompt,
        content=content,
        schema_name=schema_name,
        schema=schema,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )


def _send_chat_completion(
    *,
    model: str,
    content: list[dict[str, Any]],
    schema_name: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    max_tokens: int,
    temperature: float | None,
    system_prompt: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise OpenRouterError("缺少环境变量 OPENROUTER_API_KEY")

    messages: list[dict[str, Any]] = []
    if system_prompt and system_prompt.strip():
        messages.append(
            {
                "role": "system",
                "content": system_prompt.strip(),
            }
        )
    messages.append(
        {
            "role": "user",
            "content": content,
        }
    )

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
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
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    normalized_effort = (reasoning_effort or "").strip().lower()
    if normalized_effort in VALID_REASONING_EFFORTS:
        payload["reasoning"] = {
            "effort": normalized_effort,
            "exclude": True,
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "outer_comment_generator",
    }

    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=timeout_seconds)
        if not response.ok and "reasoning" in payload and response.status_code in {400, 404, 422}:
            fallback_payload = {key: value for key, value in payload.items() if key != "reasoning"}
            response = requests.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=fallback_payload,
                timeout=timeout_seconds,
            )
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
