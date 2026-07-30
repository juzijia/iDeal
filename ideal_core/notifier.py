from __future__ import annotations

import importlib.util
import io
import sys
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from .models import DealEvent


FAILURE_MARKERS = (
    "失败",
    "错误",
    "异常",
    "超时",
    "未配置任何",
    "未提供任何",
    "无可用推送",
    "无可用通知",
    "failed",
    "failure",
    "error",
    "exception",
    "timeout",
    "no notification channel",
    "no notifier configured",
    "invalid",
)


def _format_price(value: Decimal, currency: str) -> str:
    if value == 0:
        return "免费"
    symbols = {
        "CNY": "¥",
        "RMB": "¥",
        "USD": "$",
        "TRY": "₺",
        "EUR": "€",
        "GBP": "£",
    }
    amount = format(value.normalize(), "f")
    symbol = symbols.get(currency.upper(), "")
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}".strip()


def _index_badge(index: int) -> str:
    badges = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
    return badges[index - 1] if 1 <= index <= len(badges) else f"{index}."


def _app_store_url(app_id: str, region: str) -> str:
    return f"https://apps.apple.com/{region.lower()}/app/id{app_id}"


def load_qinglong_sender() -> Callable[[str, str], object] | None:
    for scripts_dir in (Path("/ql/data/scripts"), Path("/ql/scripts")):
        notify_path = scripts_dir / "notify.py"
        if not notify_path.exists():
            continue
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        try:
            from notify import send

            return send
        except Exception:
            spec = importlib.util.spec_from_file_location("ql_notify", notify_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.send
    return None


def render_events(events: list[DealEvent], consumer: str) -> tuple[str, str]:
    grouped: dict[str, list[DealEvent]] = defaultdict(list)
    for event in events:
        grouped[event.app.app_id].append(event)
    blocks: list[str] = []
    ordered_groups = sorted(
        grouped.values(),
        key=lambda group: max(event.score for event in group),
        reverse=True,
    )
    now = datetime.now().astimezone()
    if consumer == "watchlist":
        title = f"🔔 iDeal｜{len(ordered_groups)} 款自选 App 提醒"
    else:
        title = f"🎯 iDeal｜{len(ordered_groups)} 款优质优惠"
    regions = sorted({event.app.region.upper() for event in events})
    for index, group in enumerate(ordered_groups, 1):
        reference = max(
            group, key=lambda event: (event.app.rating_count, event.app.rating)
        )
        app = reference.app
        sources = " / ".join(
            dict.fromkeys(
                claim.source for event in group for claim in event.evidence
            )
        ) or "本地价格历史"
        rating = f"{app.rating:.1f}（{app.rating_count}）" if app.rating_count else "暂无"
        label_parts: list[str] = []
        if any(event.historical_low for event in group):
            label_parts.append("🏆 历史低价")
        if any(event.new_price == 0 for event in group):
            label_parts.append("🎁 限免")
        else:
            label_parts.append("📉 " + "/".join(dict.fromkeys(event.alert_type for event in group)))
        priority = max((event.selection_priority for event in group), default=0)
        if priority:
            label_parts.append(f"🤖 AI {priority}/10")
        label_parts.append(
            "可信度 " + max((event.confidence for event in group), default="B")
        )
        price_parts = [
            f"{event.app.region.upper()} "
            f"{_format_price(event.old_price, event.app.currency)}→"
            f"{_format_price(event.new_price, event.app.currency)}"
            for event in sorted(group, key=lambda item: item.app.region)
        ]
        extra_tags = list(dict.fromkeys(tag for event in group for tag in event.tags))
        meta_parts = [app.primary_genre_name or "未知分类"]
        if extra_tags:
            meta_parts.extend(extra_tags)
        lines = [
            f"{_index_badge(index)} {app.title}",
            "｜".join(label_parts),
            f"💰 {'｜'.join(price_parts)}",
            f"⭐ {rating}｜{'｜'.join(meta_parts)}",
        ]
        reason = next(
            (event.selection_reason for event in group if event.selection_reason), ""
        )
        if reason:
            lines.append(f"💡 {reason}")
        link_region = next(
            (
                region
                for region in ("cn", "us", "tr")
                if any(event.app.region.lower() == region for event in group)
            ),
            reference.app.region,
        )
        lines.extend(
            [
                f"🔍 Apple 已核验｜来源：{sources}",
                f"📲 {link_region.upper()} App Store｜"
                f"{_app_store_url(app.app_id, link_region)}",
            ]
        )
        blocks.append("\n".join(lines))
    header = (
        f"🕒 {now:%m-%d %H:%M}｜{' · '.join(regions)}\n"
        f"🔎 共 {len(ordered_groups)} 款，涉及 {len(events)} 个区服"
    )
    return title, header + "\n\n" + "\n\n".join(blocks)


def _split_message(body: str, limit: int = 3500) -> list[str]:
    paragraphs = body.split("\n\n")
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= limit:
            current = paragraph
        else:
            lines = paragraph.splitlines()
            current = ""
            for line in lines:
                line_candidate = line if not current else current + "\n" + line
                if len(line_candidate) > limit and current:
                    chunks.append(current)
                    current = line
                else:
                    current = line_candidate
    if current:
        chunks.append(current)
    return chunks


def _sender_reported_failure(result: object, output: str) -> bool:
    if result is False:
        return True
    if isinstance(result, dict):
        for key in ("success", "ok"):
            if key in result and not bool(result[key]):
                return True
    normalized = output.casefold()
    return any(marker in normalized for marker in FAILURE_MARKERS)


def _call_sender(sender: Callable[[str, str], object], title: str, body: str) -> bool:
    captured = io.StringIO()
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            result = sender(title, body)
    except Exception as exc:
        output = captured.getvalue()
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        print(f"  ! 青龙通知发送失败：{type(exc).__name__}: {exc}")
        return False
    output = captured.getvalue()
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if _sender_reported_failure(result, output):
        print("  ! notify.py 报告发送失败，本次不写入已提醒记录；下次将自动重试")
        return False
    return True


def send_events(events: list[DealEvent], consumer: str, dry_run: bool = False) -> bool:
    if not events:
        label = "自选监控" if consumer == "watchlist" else "优惠精选"
        print(f"  [正常] {label}：本次没有满足条件的新提醒")
        return True
    title, body = render_events(events, consumer)
    if dry_run:
        print(f"\n{title}\n{body}\n")
        return True
    sender = load_qinglong_sender()
    if sender is None:
        print("  ! 未找到青龙 notify.py，无法真正发送；以下仅输出到日志")
        print(f"\n{title}\n{body}\n")
        return False
    chunks = _split_message(body)
    print(
        f"  [发送] {title}｜{len(events)} 个区服提醒｜"
        f"{len(chunks)} 段消息"
    )
    for index, chunk in enumerate(chunks, 1):
        chunk_title = title if len(chunks) == 1 else f"{title} [{index}/{len(chunks)}]"
        if not _call_sender(sender, chunk_title, chunk):
            return False
    print(f"  [正常] 青龙通知接口已接受：{title}")
    return True
