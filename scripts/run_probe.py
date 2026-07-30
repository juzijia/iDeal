#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ideal.probe import run_probe


def main() -> None:
    parser = argparse.ArgumentParser(description="检查优惠源的可用性、时效和价格准确率")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    run_probe(rounds=max(1, args.rounds), output_dir=args.output_dir)


if __name__ == "__main__":
    main()
