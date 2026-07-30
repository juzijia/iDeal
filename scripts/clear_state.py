#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ideal.config import DB_PATH, ensure_dirs
from ideal.db import Database


LABELS = {
    "ai_cache": "AI 缓存",
    "alerts": "已提醒记录",
    "price_state": "价格状态",
    "price_observations": "价格观测",
    "source_claims": "来源线索",
}


def _show(db: Database) -> None:
    print(f"数据库：{DB_PATH}")
    for name, count in db.state_counts().items():
        print(f"  {LABELS[name]}：{count} 条")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="查看或清理 iDeal 的 AI 缓存与通知去重记录"
    )
    parser.add_argument(
        "--clear-ai-cache",
        action="store_true",
        help="清除 AI 选择结果缓存，下次重新调用 AI",
    )
    parser.add_argument(
        "--clear-alerts",
        choices=("digest", "watchlist", "all"),
        help="清除对应任务的已提醒记录，使仍符合条件的优惠可以再次推送",
    )
    args = parser.parse_args()

    ensure_dirs()
    db = Database(DB_PATH)
    db.init()
    _show(db)

    changed = False
    if args.clear_ai_cache:
        print(f"已清除 AI 缓存：{db.clear_ai_cache()} 条")
        changed = True
    if args.clear_alerts:
        print(
            f"已清除 {args.clear_alerts} 已提醒记录："
            f"{db.clear_alerts(args.clear_alerts)} 条"
        )
        changed = True

    if not changed:
        print("本次只查看，没有修改任何数据。")
    else:
        print("\n清理后：")
        _show(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
