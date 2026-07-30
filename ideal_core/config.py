from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
BUNDLED_CONFIG_DIR = PROJECT_DIR / "config"
REQUIRED_CONFIG_FILES = ("settings.json", "sources.json", "watchlist.json")


class ConfigurationError(RuntimeError):
    """Raised when iDeal cannot start safely with the current configuration."""


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
    missing = [
        name for name in REQUIRED_CONFIG_FILES
        if not (CONFIG_DIR / name).is_file()
    ]
    if missing:
        missing_text = "、".join(missing)
        raise ConfigurationError(
            f"缺少运行配置：{missing_text}。"
            "青龙订阅的“文件后缀”必须填写 py json，"
            "“依赖文件”填写 ideal_core|config；保存并重新运行订阅后再执行任务。"
        )


def load_json(name: str):
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigurationError(f"配置文件不存在：{path}")
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"配置文件无法读取或 JSON 格式错误：{path}（{exc}）"
        ) from exc


def settings() -> dict:
    ensure_dirs()
    cfg = load_json("settings.json")
    if not isinstance(cfg, dict):
        raise ConfigurationError("settings.json 顶层必须是 JSON 对象")
    env_regions = os.environ.get("IDEAL_MONITOR_REGIONS", "").strip()
    if env_regions:
        cfg["monitor_regions"] = [
            x.strip().lower() for x in env_regions.split(",") if x.strip()
        ]
    repeat_push = os.environ.get("IDEAL_REPEAT_PUSH", "0").strip()
    if repeat_push not in {"0", "1"}:
        raise ConfigurationError("IDEAL_REPEAT_PUSH 只能设置为 0 或 1")
    cfg["repeat_push"] = repeat_push == "1"
    return cfg
