#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ideal.ai import provider_status
from ideal.config import CONFIG_DIR, DATA_DIR, DB_PATH, ensure_dirs, settings
from ideal.console import banner, item, line, provider_name, stage, summary
from ideal.db import Database


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 iDeal 配置和青龙环境变量可见性")
    parser.add_argument(
        "--require-ai",
        action="store_true",
        help="没有可用 AI 配置时返回非零状态",
    )
    args = parser.parse_args()

    banner("运行环境自检")
    problems: list[str] = []

    stage(1, 3, "项目与配置")
    required = [
        ROOT / "config" / "settings.json",
        ROOT / "config" / "sources.json",
        ROOT / "config" / "watchlist.json",
        ROOT / "scripts" / "run_digest.py",
    ]
    for path in required:
        exists = path.exists()
        item(path.name, str(path), ok=exists)
        if not exists:
            problems.append(f"缺少 {path}")
    syntax_errors = []
    python_files = sorted((ROOT / "ideal").glob("*.py")) + sorted(
        (ROOT / "scripts").glob("*.py")
    )
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
    try:
        ensure_dirs()
        Database(DB_PATH).init()
        data_ok = os.access(DATA_DIR, os.W_OK)
    except OSError as exc:
        data_ok = False
        problems.append(f"数据目录不可用: {exc}")
    item("数据目录", f"{DATA_DIR}｜{'可写' if data_ok else '不可写'}", ok=data_ok)
    if not data_ok and not any(x.startswith("数据目录不可用") for x in problems):
        problems.append(f"数据目录不可写: {DATA_DIR}")
    line("配置目录", CONFIG_DIR)
    line("数据目录", DATA_DIR)
    line("数据库", DB_PATH)
    line("Python", sys.version.split()[0])

    cfg = settings()
    ai = provider_status(cfg.get("ai", {}))
    configured = set(ai["configured"])

    stage(2, 3, "AI 环境变量（只检查是否存在，不显示 Key）")
    provider_variables = [
        ("qwen", "QWEN_API_KEY / DASHSCOPE_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("custom", "IDEAL_AI_API_KEY + MODEL + BASE_URL"),
    ]
    for name, variable in provider_variables:
        item(
            provider_name(name),
            f"{variable}｜{'已检测到' if name in configured else '未检测到'}",
            ok=name in configured,
        )

    line("环境变量来源", "当前 Python 进程 os.environ")
    line("提供商模式", ai["mode"])
    line(
        "有效调用顺序",
        " → ".join(provider_name(name) for name in ai["effective"])
        or "无可用 AI 路线",
    )
    if ai["primary"]:
        line(
            "默认调用",
            f"{provider_name(ai['primary'])}｜{ai['models'].get(ai['primary'], '-')}",
        )
    else:
        problems.append("当前任务进程没有可用 AI 配置")
        line(
            "处理办法",
            "确认变量已启用，并把青龙任务命令开头的 python3 去掉后重新执行",
        )

    stage(3, 3, "青龙通知")
    notify_paths = [Path("/ql/data/scripts/notify.py"), Path("/ql/scripts/notify.py")]
    notify_path = next((path for path in notify_paths if path.exists()), None)
    item(
        "notify.py",
        str(notify_path) if notify_path else "未找到；优惠会写入日志但无法真正发送",
        ok=notify_path is not None,
    )
    if notify_path is None:
        problems.append("未找到青龙 notify.py")

    summary(
        "自检结论",
        [
            ("项目文件", "正常" if not any(x.startswith("缺少") for x in problems) else "不完整"),
            (
                "AI",
                f"可用，默认 {provider_name(ai['primary'])}"
                if ai["primary"]
                else "不可用",
            ),
            ("问题数", len(problems)),
        ],
    )
    if args.require_ai and not ai["primary"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
