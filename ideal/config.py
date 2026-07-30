from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_DIR / "config"


def _default_data_dir() -> Path:
    ql_data = Path("/ql/data")
    if ql_data.exists():
        return ql_data / "db" / "ideal"
    return PROJECT_DIR / "data"


CONFIG_DIR = Path(
    os.environ.get("IDEAL_CONFIG_DIR")
    or os.environ.get("IOS_DEALS_CONFIG_DIR")
    or DEFAULT_CONFIG_DIR
).expanduser()
DATA_DIR = Path(
    os.environ.get("IDEAL_DATA_DIR")
    or os.environ.get("IOS_DEALS_DATA_DIR")
    or _default_data_dir()
).expanduser()
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "ideal.db"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_json(name: str, default: Any = None) -> Any:
    path = CONFIG_DIR / name
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def settings() -> dict[str, Any]:
    cfg = load_json("settings.json", {})
    env_regions = (
        os.environ.get("IDEAL_MONITOR_REGIONS", "").strip()
        or os.environ.get("IOS_DEALS_MONITOR_REGIONS", "").strip()
    )
    if env_regions:
        cfg["monitor_regions"] = [
            x.strip().lower() for x in env_regions.split(",") if x.strip()
        ]
    return cfg
