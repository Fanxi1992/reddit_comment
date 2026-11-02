import json
import os
import random
import re
import time
from typing import Any

import requests
from dotenv import load_dotenv


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 1.5
VALID_REASONING_EFFORTS = {"low", "medium", "high"}
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}

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


def get_openrouter_max_retries() -> int:
    raw = os.getenv("OPENROUTER_MAX_RETRIES", "").strip()
    if not raw:
        return DEFAULT_MAX_RETRIES
    try:
        return max(0, min(6, int(raw)))
    except ValueError:
        return DEFAULT_MAX_RETRIES


def get_openrouter_retry_base_delay() -> float:
    raw = os.getenv("OPENROUTER_RETRY_BASE_DELAY_SECONDS", "").strip()
    if not raw:
        return DEFAULT_RETRY_BASE_DELAY_SECONDS
    try:
        return max(0.0, min(15.0, float(raw)))
    except ValueError:
        return DEFAULT_RETRY_BASE_DELAY_SECONDS


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
    max_retries: int | None = None,
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
        max_retries=max_retries,
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
    max_retries: int | None = None,
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
        max_retries=max_retries,
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
    max_retries: int | None = None,
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

    retries = get_openrouter_max_retries() if max_retries is None else max(0, max_retries)
    base_delay = get_openrouter_retry_base_delay()
    active_payload = dict(payload)
    last_error: OpenRouterError | None = None

    for attempt in range(retries + 1):
        if attempt > 0 and base_delay > 0:
            sleep_seconds = base_delay * (2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
            time.sleep(sleep_seconds)

        try:
            response = requests.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=active_payload,
                timeout=timeout_seconds,
            )
            if not response.ok and "reasoning" in active_payload and response.status_code in {400, 404, 422}:
                active_payload = {key: value for key, value in active_payload.items() if key != "reasoning"}
                response = requests.post(
                    OPENROUTER_API_URL,
                    headers=headers,
                    json=active_payload,
                    timeout=timeout_seconds,
                )
        except requests.RequestException as exc:
            last_error = OpenRouterError(f"OpenRouter 请求失败: {exc}")
            if attempt < retries:
                continue
            raise last_error from exc

        if not response.ok:
            last_error = OpenRouterError(_format_error_response(response))
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
                continue
            raise last_error

        try:
            response_payload = response.json()
        except ValueError as exc:
            last_error = OpenRouterError("OpenRouter 返回非 JSON 响应")
            if attempt < retries:
                continue
            raise last_error from exc

        if isinstance(response_payload, dict) and response_payload.get("error"):
            err_detail = json.dumps(response_payload["error"], ensure_ascii=False)[:600]
            last_error = OpenRouterError(f"OpenRouter 返回流内错误: {err_detail}")
            if attempt < retries:
                continue
            raise last_error

        content_text = _extract_content_text(response_payload)
        if not content_text:
            last_error = OpenRouterError("OpenRouter 未返回模型内容")
            if attempt < retries:
                continue
            raise last_error

        try:
            return _parse_json_object(content_text)
        except OpenRouterError as exc:
            last_error = exc
            if attempt < retries:
                continue
            raise last_error

    raise last_error or OpenRouterError("OpenRouter 请求失败")


def _parse_json_object(content_text: str) -> dict[str, Any]:
    cleaned = content_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except Exception as first_exc:
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx > start_idx:
            try:
                parsed = json.loads(cleaned[start_idx : end_idx + 1])
            except Exception:
                raise OpenRouterError(
                    f"OpenRouter 返回内容无法解析为 JSON: {first_exc}; content={content_text[:500]}"
                ) from first_exc
        else:
            raise OpenRouterError(
                f"OpenRouter 返回内容无法解析为 JSON: {first_exc}; content={content_text[:500]}"
            ) from first_exc

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
