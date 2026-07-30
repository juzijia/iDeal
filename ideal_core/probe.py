from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .apple import AppleLookup
from .config import (
    ConfigurationError,
    DB_PATH,
    REPORT_DIR,
    ensure_dirs,
    load_json,
    settings,
)
from .console import banner, item, line, stage, summary
from .db import Database
from .http import HttpClient
from .pipeline import _http_client, _price_matches
from .sources import SourceManager


def _age_hours(value: str) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 2)
    except ValueError:
        return None


def run_probe(rounds: int = 2, output_dir: Path | None = None) -> dict:
    ensure_dirs()
    cfg = settings()
    banner("数据源健康体检")
    source_document = load_json("sources.json")
    if not isinstance(source_document, dict):
        raise ConfigurationError("sources.json 顶层必须是 JSON 对象")
    sources = source_document.get("sources")
    if not isinstance(sources, list) or any(
        not isinstance(source, dict) for source in sources
    ):
        raise ConfigurationError("sources.json 的 sources 必须是对象数组")
    source_configs = [
        x for x in sources
        if x.get("enabled", True)
    ]
    if not source_configs:
        raise ConfigurationError(
            "sources.json 没有已启用的数据源，探针不会生成空的成功报告"
        )
    db = Database(DB_PATH)
    db.init()
    run_id = db.start_run("probe")
    rows: list[dict] = []
    try:
        line("用途", "检查来源是否稳定、内容是否新鲜，以及第三方现价能否被 Apple 证实")
        line("检查轮数", f"每个来源连续抓取 {rounds} 次")
        line("说明", "该任务不筛选 App、不调用 AI、也不发送优惠通知")
        stage(1, 2, f"检查 {len(source_configs)} 个已启用来源")
        for source_cfg in source_configs:
            samples = []
            for _ in range(max(1, rounds)):
                result = SourceManager(_http_client(cfg), [source_cfg]).fetch_all()[0]
                samples.append(result)
            last = samples[-1]
            health = last.health()
            claims = last.claims
            numeric = [
                x for x in claims
                if x.old_price is not None and x.new_price is not None
            ][: int(cfg.get("probe_official_sample_size", 5))]
            matches = 0
            checked = 0
            if numeric:
                lookup = AppleLookup(_http_client(cfg), int(cfg.get("apple_lookup_batch_size", 50)))
                by_region: dict[str, list] = {}
                for claim in numeric:
                    by_region.setdefault(claim.region, []).append(claim)
                for region, region_claims in by_region.items():
                    found = lookup.lookup_many([x.app_id for x in region_claims], region)
                    for claim in region_claims:
                        app = found.get(claim.app_id)
                        if app is None:
                            continue
                        checked += 1
                        if _price_matches(claim.new_price, app.price):
                            matches += 1
            latest_age = _age_hours(health["latest_published_at"])
            avg_latency = round(sum(x.latency_ms for x in samples) / len(samples), 2)
            success_rate = round(sum(1 for x in samples if x.ok) / len(samples), 4)
            official_match_rate = round(matches / checked, 4) if checked else None
            max_age = int(source_cfg.get("max_age_hours", cfg.get("max_claim_age_hours", 72)))
            reasons: list[str] = []
            if success_rate < 1:
                reasons.append("抓取不稳定")
            if health["item_count"] == 0:
                reasons.append("无有效条目")
            if health["app_id_rate"] < 0.8:
                reasons.append("App ID 解析率低")
            if latest_age is not None and latest_age > max_age:
                reasons.append("内容过期")
            if official_match_rate is not None and official_match_rate < 0.8:
                reasons.append("第三方价格与 Apple 不一致")
            row = {
                "name": source_cfg["name"],
                "type": source_cfg["type"],
                "url": source_cfg.get("url", ""),
                "region": source_cfg.get("region", ""),
                "rounds": len(samples),
                "success_rate": success_rate,
                "avg_latency_ms": avg_latency,
                **health,
                "latest_age_hours": latest_age,
                "official_checked": checked,
                "official_match_rate": official_match_rate,
                "status": "healthy" if not reasons else "degraded",
                "reasons": "；".join(reasons),
            }
            rows.append(row)
            db.record_source_health(run_id, health)
            match_text = (
                f"{row['official_match_rate']:.0%}（抽查 {row['official_checked']} 条）"
                if row["official_match_rate"] is not None
                else "不适用（该源不直接声明优惠现价）"
            )
            result_text = (
                "健康"
                if row["status"] == "healthy"
                else f"需关注：{row['reasons'] or '指标未达标'}"
            )
            item(
                row["name"],
                f"{result_text}｜条目 {row['item_count']}｜App ID "
                f"{row['app_id_rate']:.0%}｜Apple 价格匹配 {match_text}｜"
                f"平均 {row['avg_latency_ms']:.0f} ms",
                ok=row["status"] == "healthy",
            )

        stage(2, 2, "保存体检报告")
        target = Path(output_dir or REPORT_DIR)
        target.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rounds": rounds,
            "rows": rows,
        }
        json_path = target / "source_probe.json"
        csv_path = target / "source_probe.csv"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        fieldnames = list(rows[0].keys()) if rows else []
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(rows)
        stats = {
            "sources": len(rows),
            "healthy": sum(1 for x in rows if x["status"] == "healthy"),
            "json": str(json_path),
            "csv": str(csv_path),
        }
        db.finish_run(run_id, "success", stats)
        summary(
            "体检结论",
            [
                ("已检查来源", f"{stats['sources']} 个"),
                ("健康", f"{stats['healthy']} 个"),
                ("需关注", f"{stats['sources'] - stats['healthy']} 个"),
                ("详细 JSON", str(json_path)),
                ("表格 CSV", str(csv_path)),
                ("是否影响优惠推送", "不直接影响；用于判断是否应停用或修复数据源"),
            ],
        )
        return stats
    except Exception as exc:
        db.finish_run(run_id, "failed", {}, f"{type(exc).__name__}: {exc}")
        summary(
            "体检失败",
            [
                ("错误类型", type(exc).__name__),
                ("错误信息", str(exc)),
                ("处理建议", "检查网络和 sources.json 后重新运行"),
            ],
        )
        raise
