import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from google import genai

from app.schemas import PlannedQuery, QueryPlanGenerateRequest, QueryPlanGenerateResponse


MODEL_NAME = "gemini-3.5-flash"
ENGLISH_QUERY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\s'&+/\-]{1,118}[A-Za-z0-9?]$")

load_dotenv()


QUERY_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                    },
                    "intent": {
                        "type": "string",
                        "enum": [
                            "pain_point",
                            "recommendation",
                            "review",
                            "alternative",
                            "comparison",
                            "problem_solution",
                            "community_discussion",
                            "other",
                        ],
                    },
                    "reason": {
                        "type": "string",
                    },
                    "priority": {
                        "type": "integer",
                    },
                    "suggestedTimeRange": {
                        "type": "string",
                        "enum": ["week", "month", "all"],
                    },
                },
                "required": ["query", "intent", "reason", "priority", "suggestedTimeRange"],
            },
        }
    },
    "required": ["queries"],
}


def generate_query_plan(payload: QueryPlanGenerateRequest) -> QueryPlanGenerateResponse:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少环境变量 GEMINI_API_KEY")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=_build_prompt(payload),
        config={
            "response_mime_type": "application/json",
            "response_json_schema": QUERY_PLAN_SCHEMA,
        },
    )

    if not response.text:
        raise RuntimeError("Gemini 未返回 query 规划结果")

    try:
        raw_data = json.loads(response.text)
        response_payload = QueryPlanGenerateResponse.model_validate(raw_data)
    except Exception as exc:
        raise RuntimeError(f"Gemini 返回格式无法解析: {exc}") from exc

    cleaned_queries = _clean_queries(response_payload.queries)
    if not cleaned_queries:
        raise RuntimeError("Gemini 未生成有效英文 Reddit 搜索 query")

    return QueryPlanGenerateResponse(queries=cleaned_queries[: payload.desiredQueryCount])


def _build_prompt(payload: QueryPlanGenerateRequest) -> str:
    prompt_payload = {
        "task": "Generate realistic Reddit search queries for finding organic external word-of-mouth comment opportunities.",
        "language": "English only",
        "desired_query_count": payload.desiredQueryCount,
        "constraints": {
            "queries_are_short_search_phrases": True,
            "do_not_write_full_questions": True,
            "do_not_write_chinese": True,
            "avoid_brand_spam": True,
            "prefer_recent_opportunity_queries": True,
            "default_time_range": "week",
            "priority_scale": "1 is highest priority, 5 is lowest",
        },
        "product_context": {
            "product_name": payload.productName,
            "product_description": payload.productDescription,
            "target_audience": payload.targetAudience,
            "selling_points": payload.sellingPoints,
            "competitors": payload.competitors,
            "comment_requirements": payload.commentRequirements,
            "forbidden_topics": payload.forbiddenTopics,
        },
        "query_strategy": [
            "pain points users describe before looking for a solution",
            "recommendation requests where the product could be relevant",
            "review and experience searches for the product category",
            "alternative and comparison searches involving competitors",
            "problem-solution wording users naturally type on Reddit",
            "community discussion phrases around the category or workflow",
        ],
    }
    return json.dumps(prompt_payload, ensure_ascii=False)


def _clean_queries(queries: list[PlannedQuery]) -> list[PlannedQuery]:
    seen: set[str] = set()
    cleaned: list[PlannedQuery] = []

    for item in queries:
        query = _normalize_query(item.query)
        if not query or _looks_non_english(query):
            continue

        normalized_key = query.lower()
        if normalized_key in seen:
            continue

        seen.add(normalized_key)
        cleaned.append(
            PlannedQuery(
                query=query,
                intent=item.intent,
                reason=item.reason.strip(),
                priority=item.priority,
                suggestedTimeRange=item.suggestedTimeRange,
            )
        )

    cleaned.sort(key=lambda item: (item.priority, item.query.lower()))
    return cleaned


def _normalize_query(query: str) -> str:
    normalized = " ".join(query.strip().split())
    normalized = normalized.strip("\"'.,;: ")
    if not normalized or len(normalized.split()) > 8:
        return ""
    if not ENGLISH_QUERY_PATTERN.match(normalized):
        return ""
    return normalized


def _looks_non_english(query: str) -> bool:
    return any(ord(char) > 127 for char in query)
