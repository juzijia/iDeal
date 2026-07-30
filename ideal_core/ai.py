from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .db import Database
from .http import HttpClient
from .models import DealEvent


@dataclass(slots=True)
class AIProvider:
    name: str
    api_key: str
    model: str
    url: str


@dataclass(slots=True)
class AISelection:
    events: list[DealEvent]
    status: str
    provider: str = ""
    model: str = ""
    candidate_apps: int = 0
    selected_apps: int = 0
    errors: list[str] | None = None
    api_attempts: int = 0


def _chat_endpoint(url: str) -> str:
    clean = url.rstrip("/")
    return clean if clean.endswith("/chat/completions") else clean + "/chat/completions"


def _normalize_provider(value: str) -> str:
    aliases = {
        "bailian": "qwen",
        "dashscope": "qwen",
        "google": "gemini",
    }
    clean = value.strip().lower()
    return aliases.get(clean, clean)


def _available_providers() -> dict[str, AIProvider]:
    available: dict[str, AIProvider] = {}
    custom_key = os.environ.get("IDEAL_AI_API_KEY", "").strip()
    custom_url = os.environ.get("IDEAL_AI_BASE_URL", "").strip()
    custom_model = os.environ.get("IDEAL_AI_MODEL", "").strip()
    if custom_key and custom_url and custom_model:
        available["custom"] = AIProvider(
            "custom", custom_key, custom_model, _chat_endpoint(custom_url)
        )

    qwen_key = (
        os.environ.get("QWEN_API_KEY", "").strip()
        or os.environ.get("DASHSCOPE_API_KEY", "").strip()
    )
    if qwen_key:
        available["qwen"] = AIProvider(
            "qwen",
            qwen_key,
            os.environ.get("QWEN_MODEL", "qwen3.7-plus").strip(),
            _chat_endpoint(
                os.environ.get(
                    "QWEN_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                ).strip()
            ),
        )

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key:
        available["deepseek"] = AIProvider(
            "deepseek",
            deepseek_key,
            os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
            _chat_endpoint(
                os.environ.get(
                    "DEEPSEEK_BASE_URL",
                    "https://api.deepseek.com/chat/completions",
                ).strip()
            ),
        )

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        available["gemini"] = AIProvider(
            "gemini",
            gemini_key,
            os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip(),
            _chat_endpoint(
                os.environ.get(
                    "GEMINI_BASE_URL",
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                ).strip()
            ),
        )
    return available


def _provider_mode(ai_cfg: dict[str, Any]) -> str:
    return _normalize_provider(
        os.environ.get("IDEAL_AI_PROVIDER", "").strip()
        or str(ai_cfg.get("provider", "auto")).strip()
    )


def _provider_order(ai_cfg: dict[str, Any]) -> list[str]:
    env_order = os.environ.get("IDEAL_AI_PROVIDER_ORDER", "").strip()
    configured_order = (
        [part for part in env_order.split(",") if part.strip()]
        if env_order
        else ai_cfg.get(
            "provider_order", ["qwen", "deepseek", "gemini", "custom"]
        )
    )
    order = [_normalize_provider(str(item)) for item in configured_order]
    return list(dict.fromkeys(name for name in order if name))


def _providers(ai_cfg: dict[str, Any]) -> list[AIProvider]:
    available = _available_providers()
    requested = (
        _provider_mode(ai_cfg)
    )
    order = _provider_order(ai_cfg)
    if requested and requested != "auto":
        order = [requested]
    return [available[name] for name in order if name in available]


def provider_status(ai_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return provider diagnostics without ever exposing API key values."""
    available = _available_providers()
    execution = _providers(ai_cfg)
    return {
        "mode": _provider_mode(ai_cfg) or "auto",
        "configured": list(available),
        "order": _provider_order(ai_cfg),
        "effective": [provider.name for provider in execution],
        "primary": execution[0].name if execution else "",
        "models": {name: provider.model for name, provider in available.items()},
    }


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _group_candidates(
    events: list[DealEvent],
) -> list[tuple[str, list[DealEvent], dict[str, Any]]]:
    grouped: dict[str, list[DealEvent]] = defaultdict(list)
    for event in events:
        grouped[event.app.app_id].append(event)
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            max(x.score for x in item[1]),
            max(x.app.rating_count for x in item[1]),
        ),
        reverse=True,
    )
    output = []
    for app_id, app_events in ordered:
        reference = max(
            app_events, key=lambda x: (x.app.rating_count, x.app.rating, x.score)
        )
        regions = []
        for event in sorted(app_events, key=lambda x: x.app.region):
            discount = 0
            if event.old_price > 0:
                discount = round(
                    float((event.old_price - event.new_price) / event.old_price * 100),
                    1,
                )
            regions.append(
                {
                    "region": event.app.region.upper(),
                    "old_price": _decimal_text(event.old_price),
                    "new_price": _decimal_text(event.new_price),
                    "currency": event.app.currency,
                    "discount_percent": discount,
                    "confidence": event.confidence,
                }
            )
        candidate = {
            "app_id": app_id,
            "title": reference.app.title,
            "genre": reference.app.primary_genre_name,
            "seller": reference.app.seller,
            "rating": reference.app.rating,
            "rating_count": reference.app.rating_count,
            "version": reference.app.version,
            "updated_at": reference.app.updated_at[:10],
            "minimum_ios": reference.app.minimum_os,
            "content_rating": reference.app.content_rating,
            "description": reference.app.description[:700],
            "regions": regions,
            "historical_low": any(x.historical_low for x in app_events),
            "evidence_sources": sorted(
                {claim.source for event in app_events for claim in event.evidence}
            ),
        }
        output.append((app_id, app_events, candidate))
    return output


def _build_prompt(candidates: list[dict[str, Any]], ai_cfg: dict[str, Any]) -> str:
    min_priority = int(ai_cfg.get("minimum_priority", 8))
    preference = str(ai_cfg.get("preference", "")).strip()
    numbered = [{"index": index, **item} for index, item in enumerate(candidates, 1)]
    return f"""
你是一名极其挑剔的 iOS 优惠编辑。候选价格已经由 Apple 官方接口核验为真实，
你只负责判断“是否值得打扰用户”，绝不能修改或猜测价格。

用户偏好：
{preference}

选择规则：
1. 入选数量不设上限：优秀的都选，不优秀的一款也不要选；宁缺毋滥。
2. 优先长期实用、口碑好、仍在维护、一次买断、折扣明显的效率/工具/开发/文件/
   网络/影音处理/学习类 App。
3. 排除换皮、低质量、低评分、描述含糊、价值很低、纯娱乐噪音、壁纸表盘、
   依赖昂贵订阅、疑似 IAP 营销或折扣太小的 App。
4. 同一个 App 的多个区服是同一款，只评一次。
5. priority 为 1-10；只有 priority >= {min_priority} 才能入选。
6. reason 用中文写清楚核心用途和为什么值得看，最多 45 个汉字，不写价格。

只返回严格 JSON，不要 Markdown：
{{"selected":[{{"index":1,"priority":9,"reason":"高质量文件管理工具，持续维护且折扣明显"}}]}}

候选：
{json.dumps(numbered, ensure_ascii=False, separators=(",", ":"))}
""".strip()


def _parse_json_content(content: str) -> dict[str, Any]:
    raw = (content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("selected"), list):
        raise ValueError("AI 返回缺少 selected 数组")
    return parsed


def _validated_selection(
    response: dict[str, Any],
    group_count: int,
    min_priority: int,
) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for item in response.get("selected", []):
        try:
            index = int(item.get("index"))
            priority = int(item.get("priority"))
        except (TypeError, ValueError):
            continue
        if index < 1 or index > group_count or index in seen or priority < min_priority:
            continue
        reason = re.sub(r"\s+", " ", str(item.get("reason", "")).strip())[:90]
        if not reason:
            continue
        output.append({"index": index, "priority": priority, "reason": reason})
        seen.add(index)
    output.sort(key=lambda x: x["priority"], reverse=True)
    return output


def _apply_selection(
    groups: list[tuple[str, list[DealEvent], dict[str, Any]]],
    selected: list[dict[str, Any]],
) -> list[DealEvent]:
    output = []
    for choice in selected:
        _, events, _ = groups[choice["index"] - 1]
        for event in events:
            event.selection_reason = choice["reason"]
            event.selection_priority = choice["priority"]
            event.score += choice["priority"] * 10
        output.extend(sorted(events, key=lambda x: x.app.region))
    return output


class AISelector:
    def __init__(self, db: Database, ai_cfg: dict[str, Any]):
        self.db = db
        self.cfg = ai_cfg

    def select(self, events: list[DealEvent]) -> AISelection:
        min_priority = max(1, int(self.cfg.get("minimum_priority", 8)))
        groups = _group_candidates(events)
        if not groups:
            return AISelection([], "no_candidates")
        if not self.cfg.get("enabled", True):
            selected = [
                {"index": index, "priority": 10, "reason": "规则筛选保留"}
                for index in range(1, len(groups) + 1)
            ]
            return AISelection(
                _apply_selection(groups, selected),
                "disabled",
                candidate_apps=len(groups),
                selected_apps=len(selected),
            )

        providers = _providers(self.cfg)
        if not providers:
            return self._failure(groups, "no_provider", ["未配置可用 AI API Key"])

        prompt = _build_prompt([x[2] for x in groups], self.cfg)
        errors: list[str] = []
        api_attempts = 0
        for provider in providers:
            cache_seed = json.dumps(
                {
                    "provider": provider.name,
                    "model": provider.model,
                    "prompt": prompt,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            cache_key = hashlib.sha256(cache_seed.encode("utf-8")).hexdigest()
            cached = self.db.get_ai_cache(cache_key)
            if cached is not None:
                selected = _validated_selection(
                    cached, len(groups), min_priority
                )
                return AISelection(
                    _apply_selection(groups, selected),
                    "cache",
                    provider.name,
                    provider.model,
                    len(groups),
                    len(selected),
                    errors,
                    api_attempts,
                )
            client = HttpClient(
                timeout=int(self.cfg.get("timeout_seconds", 60)),
                retries=int(self.cfg.get("retries", 1)),
                user_agent="iDeal",
            )
            payload: dict[str, Any] = {
                "model": provider.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你只做严格的 iOS 优惠质量筛选，并输出 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": max(1200, min(8000, len(groups) * 120)),
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            if provider.name == "qwen":
                payload["enable_thinking"] = False
            elif provider.name == "deepseek":
                payload["thinking"] = {"type": "disabled"}
            elif provider.name == "gemini":
                payload.pop("temperature", None)
                payload["reasoning_effort"] = "low"
            try:
                response = client.post_json(
                    provider.url,
                    payload,
                    headers={"Authorization": f"Bearer {provider.api_key}"},
                ).json()
            except Exception as exc:
                api_attempts += client.post_attempts
                errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                continue

            api_attempts += client.post_attempts
            try:
                content = response["choices"][0]["message"]["content"]
                parsed = _parse_json_content(str(content))
                selected = _validated_selection(
                    parsed, len(groups), min_priority
                )
                self.db.set_ai_cache(
                    cache_key,
                    provider.name,
                    provider.model,
                    parsed,
                    int(self.cfg.get("cache_ttl_hours", 12)),
                )
                return AISelection(
                    _apply_selection(groups, selected),
                    "success",
                    provider.name,
                    provider.model,
                    len(groups),
                    len(selected),
                    errors,
                    api_attempts,
                )
            except Exception as exc:
                errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
        return self._failure(groups, "failed", errors, api_attempts)

    def _failure(
        self,
        groups: list[tuple[str, list[DealEvent], dict[str, Any]]],
        status: str,
        errors: list[str],
        api_attempts: int = 0,
    ) -> AISelection:
        if str(self.cfg.get("on_failure", "skip")).lower() == "top_one":
            selected = [{"index": 1, "priority": 10, "reason": "AI不可用，规则兜底首选"}]
            return AISelection(
                _apply_selection(groups, selected),
                f"{status}_fallback",
                candidate_apps=len(groups),
                selected_apps=1,
                errors=errors,
                api_attempts=api_attempts,
            )
        return AISelection(
            [],
            status,
            candidate_apps=len(groups),
            selected_apps=0,
            errors=errors,
            api_attempts=api_attempts,
        )
