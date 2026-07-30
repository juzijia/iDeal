from __future__ import annotations

from datetime import datetime
from typing import Iterable


PROVIDER_NAMES = {
    "qwen": "百炼 Qwen",
    "deepseek": "DeepSeek",
    "gemini": "Google Gemini",
    "custom": "自定义接口",
}


def provider_name(name: str) -> str:
    return PROVIDER_NAMES.get(name, name or "无")


def banner(title: str, dry_run: bool = False) -> None:
    mode = "试运行（不发送通知）" if dry_run else "正式运行"
    print("=" * 68)
    print(f"iDeal｜{title}")
    print(f"开始时间：{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}｜模式：{mode}")
    print("=" * 68)


def stage(index: int, total: int, title: str) -> None:
    print(f"\n[{index}/{total}] {title}")


def line(label: str, value: object, indent: int = 2) -> None:
    print(f"{' ' * indent}{label}：{value}")


def item(name: str, details: str, ok: bool = True) -> None:
    symbol = "[正常]" if ok else "[注意]"
    print(f"  {symbol} {name}｜{details}")


def summary(title: str, rows: Iterable[tuple[str, object]]) -> None:
    print(f"\n--- {title} ---")
    for label, value in rows:
        line(label, value)
    print("-" * 68)
