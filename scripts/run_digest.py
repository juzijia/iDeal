#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ideal.pipeline import run_digest


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取多源优惠并通过 Apple 官方接口核价")
    parser.add_argument("--dry-run", action="store_true", help="只打印提醒，不调用青龙通知")
    args = parser.parse_args()
    run_digest(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
