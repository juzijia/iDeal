#!/usr/bin/env python3
# cron "15 7-22/3 * * *"
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ideal_core.ai import provider_status
from ideal_core.config import (
    BUNDLED_CONFIG_DIR,
    CONFIG_DIR,
    ConfigurationError,
    DATA_DIR,
    DB_PATH,
    REPORT_DIR,
    ensure_dirs,
    settings,
)
from ideal_core.console import banner, item, line, provider_name, stage, summary
from ideal_core.db import Database
from ideal_core.pipeline import run_digest, run_watchlist
from ideal_core.probe import run_probe


DEFAULT_PROBE_MAX_AGE_HOURS = 8.0


def _probe_max_age_hours() -> float:
    raw = os.environ.get(
        "IDEAL_PROBE_MAX_AGE_HOURS", str(DEFAULT_PROBE_MAX_AGE_HOURS)
    ).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(
            "IDEAL_PROBE_MAX_AGE_HOURS 必须是正数"
        ) from exc
    if value <= 0:
        raise ConfigurationError("IDEAL_PROBE_MAX_AGE_HOURS 必须大于 0")
    return value


def probe_status(max_age_hours: float | None = None) -> tuple[bool, str]:
    """Return whether a successful source probe is due and the human-readable reason."""
    ensure_dirs()
    threshold = max_age_hours or _probe_max_age_hours()
    path = REPORT_DIR / "source_probe.json"
    if not path.exists():
        return True, "报告不存在"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return True, f"报告无法读取：{type(exc).__name__}"
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return True, "报告没有任何来源结果，视为无效报告"
    generated_at = str(payload.get("generated_at", "")).strip()
    if not generated_at:
        return True, "报告缺少 generated_at"
    try:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return True, "generated_at 格式无效"
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age_hours = (
        datetime.now(timezone.utc) - generated.astimezone(timezone.utc)
    ).total_seconds() / 3600
    if age_hours < -0.08:
        return True, f"报告时间位于未来（{age_hours:.2f} 小时）"
    if age_hours >= threshold:
        return True, f"报告已有 {age_hours:.2f} 小时，达到 {threshold:g} 小时阈值"
    return False, f"报告仅 {max(0.0, age_hours):.2f} 小时，未达到 {threshold:g} 小时阈值"


def _run_step(
    name: str,
    action: Callable[[], object],
    failures: list[tuple[str, str]],
) -> None:
    try:
        action()
    except Exception as exc:
        failures.append((name, f"{type(exc).__name__}: {exc}"))
        line(f"{name}失败", f"{type(exc).__name__}: {exc}")


def run_auto(dry_run: bool, rounds: int, force_probe: bool) -> int:
    ensure_dirs()
    banner("青龙统一任务", dry_run=dry_run)
    line("定时", "15 7-22/3 * * *（07:15 至 22:15，每 3 小时）")
    line(
        "重复推送",
        "开启：仍符合条件的精选优惠每轮都推送"
        if settings().get("repeat_push", False)
        else "关闭：成功推送过的同一优惠不再打扰",
    )
    failures: list[tuple[str, str]] = []

    stage(1, 3, "运行全网优惠精选")
    _run_step("优惠精选", lambda: run_digest(dry_run=dry_run), failures)

    stage(2, 3, "运行自选 App 监控")
    _run_step("自选监控", lambda: run_watchlist(dry_run=dry_run), failures)

    stage(3, 3, "判断是否需要数据源探针")
    due, reason = probe_status()
    if force_probe:
        due, reason = True, "命令行要求强制运行"
    line("判断依据", f"{REPORT_DIR / 'source_probe.json'} 中的 generated_at")
    line("探针状态", reason)
    if due:
        _run_step("数据源探针", lambda: run_probe(rounds=rounds), failures)
    else:
        line("处理结果", "本轮跳过探针")

    summary(
        "统一任务结论",
        [
            ("优惠精选", "完成" if not any(x[0] == "优惠精选" for x in failures) else "失败"),
            ("自选监控", "完成" if not any(x[0] == "自选监控" for x in failures) else "失败"),
            (
                "数据源探针",
                "完成"
                if due and not any(x[0] == "数据源探针" for x in failures)
                else "失败"
                if any(x[0] == "数据源探针" for x in failures)
                else "未到时间，已跳过",
            ),
            ("失败项", "；".join(f"{name}：{error}" for name, error in failures) or "无"),
        ],
    )
    return 1 if failures else 0


def run_self_check(require_ai: bool = False) -> int:
    banner("运行环境自检")
    problems: list[str] = []
    warnings: list[str] = []

    stage(1, 4, "项目、配置与数据目录")
    try:
        ensure_dirs()
    except (OSError, ConfigurationError) as exc:
        problems.append(f"无法准备运行目录：{exc}")
    required = [
        ROOT / "ideal.py",
        ROOT / "ideal_core" / "__init__.py",
        CONFIG_DIR / "settings.json",
        CONFIG_DIR / "sources.json",
        CONFIG_DIR / "watchlist.json",
    ]
    for path in required:
        exists = path.exists()
        item(path.name, str(path), ok=exists)
        if not exists:
            problems.append(f"缺少 {path}")
    python_files = [ROOT / "ideal.py", *sorted((ROOT / "ideal_core").glob("*.py"))]
    syntax_errors: list[str] = []
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            syntax_errors.append(f"{path.name}: {exc}")
    item(
        "Python 语法",
        f"检查 {len(python_files)} 个文件，"
        f"{'全部正常' if not syntax_errors else '发现错误：' + '；'.join(syntax_errors)}",
        ok=not syntax_errors,
    )
    problems.extend(syntax_errors)
    line("内置配置", BUNDLED_CONFIG_DIR)
    line("用户配置目录", f"{CONFIG_DIR}（订阅更新不会覆盖）")
    line("持久化数据目录", f"{DATA_DIR}（数据库、缓存、去重记录与报告）")
    line("数据库", DB_PATH)
    line("Python", sys.version.split()[0])

    stage(2, 4, "AI 环境变量（只显示是否存在）")
    try:
        cfg = settings()
        ai = provider_status(cfg.get("ai", {}))
    except Exception as exc:
        problems.append(f"配置读取失败：{exc}")
        cfg = {}
        ai = {
            "configured": [],
            "mode": "不可用",
            "effective": [],
            "primary": "",
            "models": {},
        }
    configured = set(ai["configured"])
    for name, variable in [
        ("qwen", "QWEN_API_KEY / DASHSCOPE_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("custom", "IDEAL_AI_API_KEY + IDEAL_AI_MODEL + IDEAL_AI_BASE_URL"),
    ]:
        item(
            provider_name(name),
            f"{variable}｜{'已检测到' if name in configured else '未检测到'}",
            ok=name in configured,
        )
    line("提供商模式", ai["mode"])
    line(
        "有效调用顺序",
        " → ".join(provider_name(name) for name in ai["effective"]) or "无可用路线",
    )
    if ai["primary"]:
        line(
            "默认调用",
            f"{provider_name(ai['primary'])}｜{ai['models'].get(ai['primary'], '-')}",
        )
    else:
        warnings.append("当前任务进程没有可用 AI 配置")

    stage(3, 4, "推送与探针策略")
    repeat_raw = os.environ.get("IDEAL_REPEAT_PUSH", "0").strip()
    item(
        "重复推送",
        f"IDEAL_REPEAT_PUSH={repeat_raw}｜"
        + ("每轮推送仍符合条件的精选优惠" if cfg.get("repeat_push") else "同一优惠仅成功推送一次"),
        ok=repeat_raw in {"0", "1"},
    )
    if repeat_raw not in {"0", "1"}:
        problems.append("IDEAL_REPEAT_PUSH 只能设置为 0 或 1")
    try:
        due, reason = probe_status()
        item("数据源探针", f"{'本轮应运行' if due else '尚未到期'}｜{reason}", ok=True)
    except Exception as exc:
        problems.append(f"探针配置错误：{exc}")
        item("数据源探针", str(exc), ok=False)
    notify_paths = [Path("/ql/data/scripts/notify.py"), Path("/ql/scripts/notify.py")]
    notify_path = next((path for path in notify_paths if path.exists()), None)
    item(
        "青龙 notify.py",
        str(notify_path) if notify_path else "本机未找到；青龙内应位于 /ql/data/scripts/notify.py",
        ok=notify_path is not None or not Path("/ql").exists(),
    )
    if Path("/ql").exists() and notify_path is None:
        warnings.append("青龙环境中未找到 notify.py")

    stage(4, 4, "青龙任务")
    line("建议命令", "task <订阅目录>/ideal.py（例如 task iDeal/ideal.py）")
    line("建议定时", "15 7-22/3 * * *")
    line("说明", "使用青龙生成的 task 命令，以便加载面板中的环境变量")
    try:
        Database(DB_PATH).init()
        item("SQLite", f"{DB_PATH}｜可用", ok=True)
    except OSError as exc:
        problems.append(f"数据库不可用：{exc}")
        item("SQLite", str(exc), ok=False)

    summary(
        "自检结论",
        [
            ("核心问题", "；".join(problems) or "无"),
            ("注意事项", "；".join(warnings) or "无"),
            ("AI", f"可用，默认 {provider_name(ai['primary'])}" if ai["primary"] else "不可用"),
        ],
    )
    if problems or (require_ai and not ai["primary"]):
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="iDeal：青龙中的 iOS 优惠精选、Watchlist 与数据源探针"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="auto",
        choices=("auto", "digest", "watchlist", "probe", "self-check"),
        help="默认 auto：优惠精选 + Watchlist，并按报告时间决定是否运行探针",
    )
    parser.add_argument("--dry-run", action="store_true", help="只输出通知，不真正发送")
    parser.add_argument("--rounds", type=int, default=2, help="探针连续抓取轮数")
    parser.add_argument("--force-probe", action="store_true", help="auto 模式强制运行探针")
    parser.add_argument(
        "--require-ai",
        action="store_true",
        help="self-check 模式下，没有可用 AI 时返回非零状态",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rounds = max(1, args.rounds)
    try:
        if args.mode == "auto":
            return run_auto(args.dry_run, rounds, args.force_probe)
        if args.mode == "digest":
            run_digest(dry_run=args.dry_run)
        elif args.mode == "watchlist":
            run_watchlist(dry_run=args.dry_run)
        elif args.mode == "probe":
            run_probe(rounds=rounds)
        else:
            return run_self_check(require_ai=args.require_ai)
    except ConfigurationError as exc:
        banner("启动失败")
        summary(
            "配置错误",
            [
                ("原因", str(exc)),
                ("处理", "修正配置或重新运行青龙订阅后再执行任务"),
            ],
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
