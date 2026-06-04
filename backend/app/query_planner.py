import json
import re
from typing import Any

from dotenv import load_dotenv

from app.openrouter_client import chat_json, get_openrouter_query_model
from app.schemas import PlannedQuery, QueryPlanGenerateRequest, QueryPlanGenerateResponse


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
                "additionalProperties": False,
            },
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}


def generate_query_plan(payload: QueryPlanGenerateRequest) -> QueryPlanGenerateResponse:
    raw_data = chat_json(
        model=get_openrouter_query_model(),
        prompt=_build_prompt(payload),
        schema_name="query_plan",
        schema=QUERY_PLAN_SCHEMA,
    )

    try:
        response_payload = QueryPlanGenerateResponse.model_validate(raw_data)
    except Exception as exc:
        raise RuntimeError(f"OpenRouter 返回 query 规划格式无法解析: {exc}") from exc

    cleaned_queries = _clean_queries(response_payload.queries)
    if not cleaned_queries:
        raise RuntimeError("OpenRouter 未生成有效英文 Reddit 搜索 query")

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
