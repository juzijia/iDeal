from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .http import HttpClient
from .models import Claim
from .parsers import (
    extract_app_id,
    infer_deal_kind,
    parse_app_price_transition,
    parse_datetime,
)


@dataclass(slots=True)
class SourceResult:
    source: str
    claims: list[Claim] = field(default_factory=list)
    raw_item_count: int = 0
    ok: bool = True
    latency_ms: int = 0
    error: str = ""

    def health(self) -> dict[str, Any]:
        total = self.raw_item_count
        with_id = sum(1 for x in self.claims if x.app_id)
        numeric = sum(
            1 for x in self.claims if x.old_price is not None and x.new_price is not None
        )
        published = sorted((x.published_at for x in self.claims if x.published_at), reverse=True)
        return {
            "source": self.source,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "item_count": total,
            "claim_count": len(self.claims),
            "app_id_rate": round(with_id / total, 4) if total else 0.0,
            "price_claim_rate": round(numeric / total, 4) if total else 0.0,
            "latest_published_at": published[0] if published else "",
            "error": self.error,
        }


def _node_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        value = node.findtext(name) or node.findtext(f"{{*}}{name}")
        if value:
            return value.strip()
    return ""


def _entry_link(node: ET.Element) -> str:
    direct = _node_text(node, ("link",))
    if direct:
        return direct
    for child in node.findall("{*}link"):
        href = child.attrib.get("href", "").strip()
        if href:
            return href
    return ""


def _parse_xml_entries(raw: str) -> list[dict[str, str]]:
    root = ET.fromstring(raw)
    nodes = root.findall(".//item") or root.findall(".//{*}item")
    if not nodes:
        nodes = root.findall(".//entry") or root.findall(".//{*}entry")
    entries: list[dict[str, str]] = []
    for node in nodes:
        entries.append(
            {
                "title": html.unescape(_node_text(node, ("title",))),
                "link": html.unescape(_entry_link(node)),
                "description": html.unescape(
                    _node_text(node, ("description", "summary", "content"))
                ),
                "source_id": _node_text(node, ("guid", "id")),
                "published_at": parse_datetime(
                    _node_text(node, ("pubDate", "published", "updated", "date"))
                ),
            }
        )
    return entries


def _make_claim(
    source: str,
    region: str,
    entry: dict[str, str],
    app_id: str,
    raw: dict[str, Any] | None = None,
) -> Claim:
    combined = f"{entry['title']}\n{entry['description']}"
    old_price, new_price, currency = parse_app_price_transition(combined)
    return Claim(
        source=source,
        source_id=entry.get("source_id") or entry.get("link") or entry.get("title", ""),
        app_id=app_id,
        region=region,
        title=entry.get("title", "").strip(),
        url=entry.get("link", "").strip(),
        published_at=entry.get("published_at", ""),
        deal_kind=infer_deal_kind(combined, old_price, new_price),
        old_price=old_price,
        new_price=new_price,
        currency=currency,
        description=entry.get("description", "")[:4000],
        raw=raw or {},
    )


class SourceManager:
    def __init__(
        self,
        client: HttpClient,
        source_configs: list[dict[str, Any]],
        max_workers: int = 5,
    ):
        self.client = client
        self.source_configs = source_configs
        self.max_workers = max(1, max_workers)

    def fetch_all(self) -> list[SourceResult]:
        configs = [x for x in self.source_configs if x.get("enabled", True)]
        if not configs:
            return []
        ordered: dict[int, SourceResult] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(configs))) as pool:
            futures = {
                pool.submit(self._fetch_one, cfg): index
                for index, cfg in enumerate(configs)
            }
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
        return [ordered[index] for index in range(len(configs))]

    def _fetch_one(self, cfg: dict[str, Any]) -> SourceResult:
        started = time.perf_counter()
        name = str(cfg.get("name") or cfg.get("type") or "source")
        try:
            claims, raw_item_count = self._fetch(cfg)
            return SourceResult(
                source=name,
                claims=claims,
                raw_item_count=raw_item_count,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return SourceResult(
                source=name,
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _fetch(self, cfg: dict[str, Any]) -> tuple[list[Claim], int]:
        source_type = cfg.get("type", "rss")
        if source_type in {"rss", "appstore_discounts", "freshapps"}:
            return self._fetch_rss(cfg)
        if source_type == "apple_chart":
            return self._fetch_apple_chart(cfg)
        raise ValueError(f"不支持的源类型: {source_type}")

    def _fetch_rss(self, cfg: dict[str, Any]) -> tuple[list[Claim], int]:
        name = str(cfg["name"])
        region = str(cfg.get("region", "us")).lower()
        urls = cfg.get("urls") or [cfg["url"]]
        response = None
        errors: list[str] = []
        for url in urls:
            try:
                response = self.client.get(str(url))
                break
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        if response is None:
            raise RuntimeError("；".join(errors))
        entries = _parse_xml_entries(response.text)
        claims: list[Claim] = []
        eligible_count = 0
        resolve_limit = int(cfg.get("resolve_detail_limit", 0))
        resolved = 0
        for entry in entries[: int(cfg.get("max_items", 100))]:
            if cfg.get("type") == "appstore_discounts":
                sid = entry.get("source_id", "").lower()
                if sid.startswith(("welcome", "ranking")):
                    continue
            eligible_count += 1
            combined = f"{entry['link']} {entry['description']}"
            app_id = extract_app_id(combined)
            if not app_id and resolve_limit and resolved < resolve_limit and entry["link"]:
                resolved += 1
                try:
                    detail = self.client.get(entry["link"]).text
                    app_id = extract_app_id(detail)
                except Exception:
                    app_id = ""
            if not app_id:
                continue
            claim = _make_claim(name, region, entry, app_id)
            if cfg.get("type") == "appstore_discounts":
                marker = re.search(
                    r"Discount\s+Information\s*\r?\n\s*#{1,4}\s*In-App Purchases",
                    entry["description"],
                    flags=re.I,
                )
                if marker and claim.old_price is None:
                    claim.deal_kind = "iap"
            claims.append(claim)
        return claims, eligible_count

    def _fetch_apple_chart(self, cfg: dict[str, Any]) -> tuple[list[Claim], int]:
        region = str(cfg.get("region", "us")).lower()
        chart = str(cfg.get("chart", "top-paid"))
        limit = max(10, min(int(cfg.get("limit", 100)), 100))
        url = f"https://rss.marketingtools.apple.com/api/v2/{region}/apps/{chart}/{limit}/apps.json"
        data = self.client.get(url).json()
        claims: list[Claim] = []
        results = data.get("feed", {}).get("results", [])
        for item in results:
            app_id = str(item.get("id", "")).strip() or extract_app_id(str(item.get("url", "")))
            if not app_id:
                continue
            claims.append(
                Claim(
                    source=str(cfg["name"]),
                    source_id=app_id,
                    app_id=app_id,
                    region=region,
                    title=str(item.get("name", "")),
                    url=str(item.get("url", "")),
                    deal_kind="discovery",
                    description=" | ".join(item.get("genreNames", []) or []),
                    raw=item,
                )
            )
        return claims, len(results)
