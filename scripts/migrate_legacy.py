#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ideal.config import CONFIG_DIR, DB_PATH, ensure_dirs
from ideal.db import Database


def migrate_watchlist(source: Path, destination: Path) -> int:
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    output = []
    for item in data.get("apps", []):
        output.append(
            {
                "id": str(item.get("id", "")),
                "name": item.get("name", ""),
                "regions": item.get("regions") or item.get("countries") or ["us"],
                "enabled": item.get("enabled", True),
                "target_prices": item.get("target_prices", {}),
                "notify_on_any_drop": item.get("notify_on_any_drop", True),
                "notify_on_free": item.get("notify_on_free", True),
                "tags": item.get("tags", []),
                "legacy_target_price_for_manual_review": item.get("target_price"),
            }
        )
    destination.write_text(
        json.dumps({"apps": output}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(output)


def read_legacy_history(path: Path):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            """
            SELECT app_id, region, price, currency, captured_at
            FROM price_history
            WHERE app_id IS NOT NULL AND region IS NOT NULL AND price IS NOT NULL
            ORDER BY captured_at, id
            """
        ).fetchall()
        return [
            (str(app_id), str(region).lower(), Decimal(str(price)), str(currency or ""), str(captured_at))
            for app_id, region, price, currency, captured_at in rows
        ]
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="将旧版 Watchlist/价格历史安全迁移到 iDeal")
    parser.add_argument("--legacy-dir", type=Path, required=True)
    parser.add_argument("--legacy-db", type=Path)
    parser.add_argument(
        "--replace-watchlist",
        action="store_true",
        help="用迁移结果替换 iDeal watchlist；替换前自动备份",
    )
    parser.add_argument("--import-history", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    source_watchlist = args.legacy_dir / "watchlist_ids.json"
    migrated = CONFIG_DIR / "watchlist.migrated.json"
    count = migrate_watchlist(source_watchlist, migrated)
    print(f"已生成 {migrated}，共 {count} 个 App。旧版全局目标价仅保留待人工复核。")
    if args.replace_watchlist:
        active = CONFIG_DIR / "watchlist.json"
        if active.exists():
            shutil.copy2(active, CONFIG_DIR / "watchlist.before-migration.json")
        shutil.copy2(migrated, active)
        print(f"已替换 {active}")
    if args.import_history:
        legacy_db = args.legacy_db or Path("/ql/data/db/ios_deals.db")
        db = Database(DB_PATH)
        db.init()
        imported = db.import_legacy_observations(read_legacy_history(legacy_db))
        print(f"已按时间顺序导入 {imported} 条旧价格历史。")


if __name__ == "__main__":
    main()
