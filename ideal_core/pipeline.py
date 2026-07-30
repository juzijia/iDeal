from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .ai import AISelector, provider_status
from .apple import AppleLookup
from .config import DB_PATH, ensure_dirs, load_json, settings
from .console import banner, item, line, provider_name, stage, summary
from .db import Database
from .http import HttpClient
from .models import Claim, DealEvent, OfficialApp, Transition
from .notifier import send_events
from .sources import SourceManager


CONFIDENCE_RANK = {"C": 1, "B": 2, "A": 3}


def _http_client(cfg: dict) -> HttpClient:
    http_cfg = cfg.get("http", {})
    return HttpClient(
        timeout=int(http_cfg.get("timeout_seconds", 20)),
        retries=int(http_cfg.get("retries", 2)),
        user_agent=str(http_cfg.get("user_agent", "iDeal")),
    )


def _fresh(claim: Claim, max_age_hours: int) -> bool:
    if not claim.published_at:
        return False
    try:
        published = datetime.fromisoformat(claim.published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published >= datetime.now(timezone.utc) - timedelta(hours=max_age_hours)


def _price_matches(first: Decimal | None, second: Decimal) -> bool:
    return first is not None and abs(first - second) <= Decimal("0.001")


def _genre_allowed(app: OfficialApp, cfg: dict) -> bool:
    blocked = {str(x) for x in cfg.get("blocked_primary_genre_ids", [])}
    allowed = {str(x) for x in cfg.get("allowed_primary_genre_ids", [])}
    if app.primary_genre_id in blocked:
        return False
    return not allowed or app.primary_genre_id in allowed


def _score(
    app: OfficialApp,
    old_price: Decimal,
    evidence: list[Claim],
    confidence: str,
    historical_low: bool,
) -> int:
    score = {"A": 50, "B": 38, "C": 20}[confidence]
    if app.price == 0:
        score += 18
    elif old_price > 0:
        score += min(18, int(((old_price - app.price) / old_price) * 20))
    score += min(10, len({x.source for x in evidence}) * 3)
    score += min(8, app.rating_count // 1000)
    if historical_low:
        score += 8
    return score


def _event_from_evidence(
    app: OfficialApp,
    transition: Transition,
    evidence: list[Claim],
    cfg: dict,
    consumer: str,
) -> DealEvent | None:
    fresh_claims = [
        claim
        for claim in evidence
        if claim.deal_kind in {"free", "discount"}
        and claim.old_price is not None
        and claim.new_price is not None
        and claim.old_price > claim.new_price
        and _price_matches(claim.new_price, app.price)
        and (not claim.currency or claim.currency.upper() == app.currency.upper())
        and _fresh(
            claim,
            int(
                cfg.get("_source_max_age", {}).get(
                    claim.source, cfg.get("max_claim_age_hours", 72)
                )
            ),
        )
    ]
    if transition.is_drop:
        confidence = "A"
        old_price = transition.previous_price
        event_seed = f"state:{transition.cycle}"
    elif fresh_claims:
        confidence = "B"
        old_price = max(x.old_price for x in fresh_claims if x.old_price is not None)
        event_seed = "claim:" + hashlib.sha256(
            "|".join(sorted(x.claim_key for x in fresh_claims)).encode("utf-8")
        ).hexdigest()
    else:
        return None
    minimum = str(cfg.get("minimum_confidence", "B")).upper()
    if CONFIDENCE_RANK[confidence] < CONFIDENCE_RANK.get(minimum, 2):
        return None
    if old_price is None or old_price <= app.price:
        return None
    event_key = f"{consumer}:{app.app_id}:{app.region}:{event_seed}"
    alert_type = "限免" if app.price == 0 else "降价"
    return DealEvent(
        event_key=event_key,
        app=app,
        old_price=old_price,
        new_price=app.price,
        alert_type=alert_type,
        confidence=confidence,
        score=_score(app, old_price, fresh_claims or evidence, confidence, transition.is_new_low),
        evidence=evidence,
        historical_low=transition.is_new_low,
    )


def run_digest(dry_run: bool = False) -> dict:
    run_started = time.perf_counter()
    ensure_dirs()
    cfg = settings()
    banner("全网优惠精选", dry_run=dry_run)
    db = Database(DB_PATH)
    db.init()
    run_id = db.start_run("digest")
    stats: dict = {}
    try:
        monitor_regions = {
            str(x).lower() for x in cfg.get("monitor_regions", ["us", "cn", "tr"])
        }
        line("监控区服", "、".join(region.upper() for region in sorted(monitor_regions)))
        line("最低可信度", str(cfg.get("minimum_confidence", "B")).upper())
        line("AI 优质门槛", f"{cfg.get('ai', {}).get('minimum_priority', 8)}/10")
        repeat_push = bool(cfg.get("repeat_push", False))
        line(
            "重复推送",
            "开启：仍符合条件的精选优惠每轮都推送"
            if repeat_push
            else "关闭：成功推送过的同一优惠不再推送",
        )
        stage(1, 5, "抓取优惠来源")
        source_started = time.perf_counter()
        source_cfg = [
            source
            for source in load_json("sources.json", {}).get("sources", [])
            if not source.get("region")
            or str(source.get("region")).lower() in monitor_regions
        ]
        effective_cfg = dict(cfg)
        effective_cfg["_source_max_age"] = {
            str(source.get("name")): int(
                source.get("max_age_hours", cfg.get("max_claim_age_hours", 72))
            )
            for source in source_cfg
        }
        source_results = SourceManager(
            _http_client(cfg),
            source_cfg,
            max_workers=int(cfg.get("source_workers", 5)),
        ).fetch_all()
        claims = [claim for result in source_results for claim in result.claims if claim.app_id]
        db.record_claims(claims)
        for result in source_results:
            db.record_source_health(run_id, result.health())
            state = (
                "正常"
                if result.ok and result.raw_item_count
                else "本轮无条目"
                if result.ok
                else "抓取失败"
            )
            item(
                result.source,
                f"{state}｜原始 {result.raw_item_count} 条｜有效线索 "
                f"{len(result.claims)} 条｜{result.latency_ms} ms",
                ok=result.ok and bool(result.raw_item_count),
            )
            if result.error:
                line("失败原因", result.error, indent=6)
        line(
            "抓取小结",
            f"{len(source_results)} 个源，"
            f"{sum(1 for result in source_results if result.ok)} 个可访问，"
            f"得到 {len(claims)} 条带 App ID 的线索",
        )
        source_seconds = time.perf_counter() - source_started
        line("阶段耗时", f"{source_seconds:.2f} 秒")

        grouped: dict[tuple[str, str], list[Claim]] = defaultdict(list)
        for claim in claims:
            grouped[(claim.app_id, claim.region)].append(claim)

        stage(2, 5, "Apple 官方核验")
        line("待核验", f"{len(grouped)} 个 App×区服组合")
        apple_started = time.perf_counter()
        official: dict[tuple[str, str], OfficialApp] = {}
        apple_request_stats: list[dict[str, object]] = []
        regions = sorted({region for _, region in grouped})
        apple_batch_size = max(
            1, min(int(cfg.get("apple_lookup_batch_size", 100)), 100)
        )

        def lookup_region(region: str):
            ids = [app_id for app_id, item_region in grouped if item_region == region]
            lookup = AppleLookup(_http_client(cfg), apple_batch_size)
            found = lookup.lookup_many(ids, region)
            return region, found, lookup.request_stats()

        with ThreadPoolExecutor(max_workers=min(4, len(regions) or 1)) as pool:
            futures = [pool.submit(lookup_region, region) for region in regions]
            for future in as_completed(futures):
                region, found, request_stats = future.result()
                apple_request_stats.append(request_stats)
                for app_id, app in found.items():
                    if app is not None:
                        official[(app_id, region)] = app

        apple_seconds = time.perf_counter() - apple_started
        apple_batches = sum(
            int(request_stats["logical_batches"])
            for request_stats in apple_request_stats
        )
        apple_network_attempts = sum(
            int(request_stats["network_attempts"])
            for request_stats in apple_request_stats
        )
        batch_details = [
            batch
            for request_stats in apple_request_stats
            for batch in request_stats["batches"]
        ]
        batch_details.sort(key=lambda batch: (str(batch["region"]), int(batch["ids"])))
        line(
            "请求方式",
            f"{len(grouped)} 个 App×区服按区服分组，每批最多 "
            f"{apple_batch_size} 个 ID",
        )
        line("Apple 逻辑批次", f"{apple_batches} 批")
        line("实际网络尝试", f"{apple_network_attempts} 次（含自动重试）")
        if batch_details:
            line(
                "批次明细",
                "；".join(
                    f"{batch['region']} {batch['ids']}个/"
                    f"{batch['elapsed_ms']}ms/"
                    f"{batch['attempts']}次"
                    for batch in batch_details
                ),
            )
        line("核验成功", f"{len(official)} 个")
        line("Apple 未返回", f"{max(0, len(grouped) - len(official))} 个")
        line("阶段耗时", f"{apple_seconds:.2f} 秒")

        stage(3, 5, "规则过滤与去重")
        rules_started = time.perf_counter()
        events: list[DealEvent] = []
        filtered = Counter()
        for key, app in official.items():
            transition = db.observe(app, run_id, "digest")
            if not _genre_allowed(app, cfg):
                filtered["category"] += 1
                continue
            event = _event_from_evidence(
                app, transition, grouped[key], effective_cfg, consumer="digest"
            )
            if event is None:
                filtered["not_deal"] += 1
                continue
            if not repeat_push and db.was_alerted(event.event_key):
                filtered["duplicate"] += 1
                continue
            events.append(event)

        events.sort(key=lambda x: (x.score, x.app.rating_count), reverse=True)
        candidate_events = list(events)
        line("分类不符合偏好", filtered["category"])
        line("不是新鲜且可核验的真实降价", filtered["not_deal"])
        line(
            "已经提醒过",
            f"{filtered['duplicate']}（已跳过）"
            if not repeat_push
            else "忽略历史记录，本轮仍符合条件的全部保留",
        )
        line(
            "交给 AI",
            f"{len({event.app.app_id for event in candidate_events})} 款 App，"
            f"涉及 {len(candidate_events)} 个区服",
        )
        rules_seconds = time.perf_counter() - rules_started
        line("阶段耗时", f"{rules_seconds:.2f} 秒")

        stage(4, 5, "AI 严格精选")
        ai_started = time.perf_counter()
        ai_cfg = cfg.get("ai", {})
        ai_runtime = provider_status(ai_cfg)
        configured_text = "、".join(
            provider_name(name) for name in ai_runtime["configured"]
        ) or "未检测到任何 API Key"
        route_text = " → ".join(
            provider_name(name) for name in ai_runtime["effective"]
        ) or "无可用路线"
        line("当前进程检测到", configured_text)
        line("环境变量来源", "当前 Python 进程 os.environ")
        line("运行模式", ai_runtime["mode"])
        line("实际调用顺序", route_text)
        if ai_runtime["primary"]:
            primary = ai_runtime["primary"]
            line(
                "默认调用",
                f"{provider_name(primary)}｜{ai_runtime['models'].get(primary, '-')}",
            )
        elif candidate_events:
            line(
                "配置问题",
                "青龙当前任务进程看不到 QWEN_API_KEY、DEEPSEEK_API_KEY 或 GEMINI_API_KEY",
            )
        ai_selection = AISelector(db, ai_cfg).select(candidate_events)
        events = ai_selection.events
        ai_labels = {
            "success": "调用成功",
            "cache": "命中 12 小时缓存，本次未调用 API",
            "no_candidates": "没有候选，无需调用",
            "no_provider": "没有可用 AI 配置",
            "failed": "所有 AI 调用均失败",
            "disabled": "AI 已关闭",
        }
        line("AI 结果", ai_labels.get(ai_selection.status, ai_selection.status))
        if ai_selection.provider:
            line(
                "完成服务",
                f"{provider_name(ai_selection.provider)}｜{ai_selection.model}",
            )
        line(
            "精选结果",
            f"{ai_selection.candidate_apps} 款候选，"
            f"{ai_selection.selected_apps} 款达到优质门槛",
        )
        line("外部 AI 请求", f"{ai_selection.api_attempts} 次（含自动重试）")
        for error in ai_selection.errors or []:
            line("回退/失败原因", error)
        ai_seconds = time.perf_counter() - ai_started
        line("阶段耗时", f"{ai_seconds:.2f} 秒")

        stage(5, 5, "生成并发送通知")
        notify_started = time.perf_counter()
        delivered = send_events(events, "digest", dry_run=dry_run)
        if delivered and not dry_run:
            db.record_alerts("digest", events)
        notify_seconds = time.perf_counter() - notify_started
        total_seconds = time.perf_counter() - run_started
        line("阶段耗时", f"{notify_seconds:.2f} 秒")
        stats = {
            "sources": len(source_results),
            "source_failures": sum(1 for x in source_results if not x.ok),
            "claims": len(claims),
            "verified": len(official),
            "eligible_before_ai": len(candidate_events),
            "candidate_apps_before_ai": len({x.app.app_id for x in candidate_events}),
            "selected_apps": ai_selection.selected_apps,
            "alert_regions": len(events),
            "ai_status": ai_selection.status,
            "ai_provider": ai_selection.provider,
            "ai_network_attempts": ai_selection.api_attempts,
            "apple_lookup_batches": apple_batches,
            "apple_network_attempts": apple_network_attempts,
            "stage_seconds": {
                "sources": round(source_seconds, 3),
                "apple": round(apple_seconds, 3),
                "rules": round(rules_seconds, 3),
                "ai": round(ai_seconds, 3),
                "notification": round(notify_seconds, 3),
                "total": round(total_seconds, 3),
            },
            "delivered": delivered,
            "dry_run": dry_run,
        }
        retention = cfg.get("retention_days", {})
        db.cleanup(
            observation_days=int(retention.get("observations", 180)),
            claim_days=int(retention.get("claims", 30)),
            health_days=int(retention.get("health", 90)),
            alert_days=int(retention.get("alerts", 365)),
        )
        db.finish_run(run_id, "success", stats)
        notification_result = (
            "本轮无优质新优惠，无需发送"
            if not events
            else "试运行，仅打印内容"
            if dry_run
            else "发送成功"
            if delivered
            else "发送失败，请查看上方原因"
        )
        summary(
            "本次运行结论",
            [
                ("来源", f"{len(source_results)} 个，失败 {stats['source_failures']} 个"),
                ("有效线索", f"{len(claims)} 条"),
                (
                    "Apple 核验",
                    f"{len(official)} 个 App×区服 / "
                    f"{apple_batches} 批 / {apple_network_attempts} 次网络尝试",
                ),
                (
                    "AI 前候选",
                    f"{stats['candidate_apps_before_ai']} 款 App / "
                    f"{stats['eligible_before_ai']} 个区服",
                ),
                ("AI 入选", f"{ai_selection.selected_apps} 款 App"),
                ("AI 服务", provider_name(ai_selection.provider) if ai_selection.provider else "未调用"),
                ("AI 网络请求", f"{ai_selection.api_attempts} 次"),
                ("通知", notification_result),
                ("总耗时", f"{total_seconds:.2f} 秒"),
            ],
        )
        return stats
    except Exception as exc:
        db.finish_run(run_id, "failed", stats, f"{type(exc).__name__}: {exc}")
        summary(
            "运行失败",
            [
                ("错误类型", type(exc).__name__),
                ("错误信息", str(exc)),
                ("处理建议", "查看上方最后一个阶段；修复后重新执行本任务"),
            ],
        )
        raise


def _target_for(app_cfg: dict, region: str) -> Decimal | None:
    value = (app_cfg.get("target_prices") or {}).get(region)
    if value is None:
        return None
    return Decimal(str(value))


def run_watchlist(dry_run: bool = False) -> dict:
    run_started = time.perf_counter()
    ensure_dirs()
    cfg = settings()
    banner("自选 App 价格监控", dry_run=dry_run)
    db = Database(DB_PATH)
    db.init()
    run_id = db.start_run("watchlist")
    stats: dict = {}
    try:
        stage(1, 3, "读取监控清单并查询 Apple")
        apple_started = time.perf_counter()
        apps_cfg = [
            x for x in load_json("watchlist.json", {}).get("apps", [])
            if x.get("enabled", True) and str(x.get("id", "")).strip()
        ]
        plan: dict[str, list[str]] = defaultdict(list)
        app_config_by_key: dict[tuple[str, str], dict] = {}
        for item in apps_cfg:
            app_id = str(item["id"])
            for region in item.get("regions", ["us"]):
                region = str(region).lower()
                plan[region].append(app_id)
                app_config_by_key[(app_id, region)] = item

        line("已启用 App", f"{len(apps_cfg)} 款")
        line(
            "查询区服",
            "、".join(region.upper() for region in sorted(plan)) or "无",
        )
        line(
            "待查询",
            f"{sum(len(ids) for ids in plan.values())} 个 App×区服组合",
        )
        events: list[DealEvent] = []
        unavailable: list[str] = []
        verified = 0
        no_new_alert = 0
        duplicate_alert = 0
        found_by_region: dict[str, dict[str, OfficialApp | None]] = {}
        apple_request_stats: list[dict[str, object]] = []
        apple_batch_size = max(
            1, min(int(cfg.get("apple_lookup_batch_size", 100)), 100)
        )

        def lookup_watchlist_region(region: str, ids: list[str]):
            lookup = AppleLookup(_http_client(cfg), apple_batch_size)
            found = lookup.lookup_many(ids, region)
            return region, found, lookup.request_stats()

        with ThreadPoolExecutor(max_workers=min(4, len(plan) or 1)) as pool:
            futures = [
                pool.submit(lookup_watchlist_region, region, ids)
                for region, ids in plan.items()
            ]
            for future in as_completed(futures):
                region, found, request_stats = future.result()
                found_by_region[region] = found
                apple_request_stats.append(request_stats)

        stage(2, 3, "比较价格变化")
        compare_started = time.perf_counter()
        for region, found in found_by_region.items():
            for app_id, app in found.items():
                if app is None:
                    unavailable.append(f"{app_id}@{region.upper()}")
                    continue
                verified += 1
                transition = db.observe(app, run_id, "watchlist")
                item_cfg = app_config_by_key[(app_id, region)]
                target = _target_for(item_cfg, region)
                alert_type = ""
                if (
                    item_cfg.get("notify_on_free", True)
                    and transition.previous_price is not None
                    and transition.previous_price > 0
                    and app.price == 0
                ):
                    alert_type = "限免"
                elif (
                    target is not None
                    and transition.previous_price is not None
                    and transition.previous_price > target
                    and app.price <= target
                ):
                    alert_type = "达到目标价"
                elif item_cfg.get("notify_on_any_drop", True) and transition.is_drop:
                    alert_type = "降价"
                if not alert_type or transition.previous_price is None:
                    no_new_alert += 1
                    continue
                event_key = (
                    f"watchlist:{app_id}:{region}:state:{transition.cycle}:{alert_type}"
                )
                event = DealEvent(
                    event_key=event_key,
                    app=app,
                    old_price=transition.previous_price,
                    new_price=app.price,
                    alert_type=alert_type,
                    confidence="A",
                    score=100,
                    historical_low=transition.is_new_low,
                    tags=list(item_cfg.get("tags", [])),
                )
                if not db.was_alerted(event_key):
                    events.append(event)
                else:
                    duplicate_alert += 1

        apple_seconds = compare_started - apple_started
        apple_batches = sum(
            int(request_stats["logical_batches"])
            for request_stats in apple_request_stats
        )
        apple_network_attempts = sum(
            int(request_stats["network_attempts"])
            for request_stats in apple_request_stats
        )
        line(
            "Apple 请求",
            f"{apple_batches} 批 / {apple_network_attempts} 次网络尝试（含自动重试）",
        )
        line("Apple 查询耗时", f"{apple_seconds:.2f} 秒")
        line("Apple 查询成功", f"{verified} 个")
        line("不可用", f"{len(unavailable)} 个")
        if unavailable:
            line("不可用明细", "、".join(unavailable))
        line("价格未触发提醒", f"{no_new_alert} 个")
        line("已提醒过", f"{duplicate_alert} 个")
        line("本轮新提醒", f"{len(events)} 个")
        compare_seconds = time.perf_counter() - compare_started
        line("比较阶段耗时", f"{compare_seconds:.2f} 秒")

        stage(3, 3, "生成并发送通知")
        notify_started = time.perf_counter()
        delivered = send_events(events, "watchlist", dry_run=dry_run)
        if delivered and not dry_run:
            db.record_alerts("watchlist", events)
        notify_seconds = time.perf_counter() - notify_started
        total_seconds = time.perf_counter() - run_started
        line("阶段耗时", f"{notify_seconds:.2f} 秒")
        stats = {
            "configured_apps": len(apps_cfg),
            "verified_regions": verified,
            "unavailable": unavailable,
            "alerts": len(events),
            "apple_lookup_batches": apple_batches,
            "apple_network_attempts": apple_network_attempts,
            "stage_seconds": {
                "apple": round(apple_seconds, 3),
                "compare": round(compare_seconds, 3),
                "notification": round(notify_seconds, 3),
                "total": round(total_seconds, 3),
            },
            "delivered": delivered,
            "dry_run": dry_run,
        }
        db.finish_run(run_id, "success", stats)
        notification_result = (
            "没有新的价格变化，无需发送"
            if not events
            else "试运行，仅打印内容"
            if dry_run
            else "发送成功"
            if delivered
            else "发送失败，请查看上方原因"
        )
        summary(
            "本次运行结论",
            [
                ("监控 App", f"{len(apps_cfg)} 款"),
                (
                    "Apple 核验",
                    f"{verified} 个 App×区服 / "
                    f"{apple_batches} 批 / {apple_network_attempts} 次网络尝试",
                ),
                ("本轮新提醒", f"{len(events)} 个"),
                ("通知", notification_result),
                ("总耗时", f"{total_seconds:.2f} 秒"),
            ],
        )
        return stats
    except Exception as exc:
        db.finish_run(run_id, "failed", stats, f"{type(exc).__name__}: {exc}")
        summary(
            "运行失败",
            [
                ("错误类型", type(exc).__name__),
                ("错误信息", str(exc)),
                ("处理建议", "检查 watchlist.json 和网络后重新执行"),
            ],
        )
        raise
