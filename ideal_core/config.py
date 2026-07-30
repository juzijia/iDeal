from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent.parent
BUNDLED_CONFIG_DIR = PROJECT_DIR / "config"


def _default_data_dir() -> Path:
    ql_data = Path("/ql/data")
    if ql_data.exists():
        return ql_data / "db" / "ideal"
    return PROJECT_DIR / "data"


DATA_DIR = Path(
    os.environ.get("IDEAL_DATA_DIR")
    or _default_data_dir()
).expanduser()
CONFIG_DIR = Path(
    os.environ.get("IDEAL_CONFIG_DIR")
    or (DATA_DIR / "config" if Path("/ql/data").exists() else BUNDLED_CONFIG_DIR)
).expanduser()
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "ideal.db"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_DIR.resolve() != BUNDLED_CONFIG_DIR.resolve():
        for source in BUNDLED_CONFIG_DIR.glob("*.json"):
            target = CONFIG_DIR / source.name
            if not target.exists():
                shutil.copy2(source, target)


def load_json(name: str, default: Any = None) -> Any:
    path = CONFIG_DIR / name
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def settings() -> dict[str, Any]:
    ensure_dirs()
    cfg = load_json("settings.json", {})
    env_regions = os.environ.get("IDEAL_MONITOR_REGIONS", "").strip()
    if env_regions:
        cfg["monitor_regions"] = [
            x.strip().lower() for x in env_regions.split(",") if x.strip()
        ]
    repeat_push = os.environ.get("IDEAL_REPEAT_PUSH", "0").strip()
    if repeat_push not in {"0", "1"}:
        raise ValueError("IDEAL_REPEAT_PUSH 只能设置为 0 或 1")
    cfg["repeat_push"] = repeat_push == "1"
    return cfg
