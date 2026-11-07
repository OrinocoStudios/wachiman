"""Where Wachiman keeps its files, and how it reads its settings.

Nothing is hard-coded to one user or one machine. Every path comes from the
environment:

    WACHIMAN_HOME   data folder               (default: $HOME/.wachiman)
    WACHIMAN_DB     radar history database    (default: $WACHIMAN_HOME/radar.db)
    WACHIMAN_GEO_DB IP -> company/country db  (default: $WACHIMAN_HOME/geo.db)

Settings are read from three layers, later layers win:

    1. built-in defaults (DEFAULTS below)
    2. $WACHIMAN_HOME/config.env   (KEY=VALUE lines, '#' comments)
    3. environment variables starting with WACHIMAN_
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, Optional

DEFAULTS: Dict[str, str] = {
    "WACHIMAN_RADAR_INTERVAL": "30",
    "WACHIMAN_TELEGRAM_BOT_TOKEN": "",
    "WACHIMAN_TELEGRAM_CHAT_ID": "",
}


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _expand(value: str, env: Mapping[str, str]) -> Path:
    """Expand a leading ~ against the given environment's HOME, not the process'."""
    if value == "~" or value.startswith("~/"):
        base = env.get("HOME") or os.path.expanduser("~")
        value = base + value[1:]
    return Path(value)


def home(env: Optional[Mapping[str, str]] = None) -> Path:
    e = _env(env)
    if e.get("WACHIMAN_HOME"):
        return _expand(e["WACHIMAN_HOME"], e)
    base = e.get("HOME") or os.path.expanduser("~")
    return Path(base) / ".wachiman"


def db_path(env: Optional[Mapping[str, str]] = None) -> Path:
    e = _env(env)
    if e.get("WACHIMAN_DB"):
        return _expand(e["WACHIMAN_DB"], e)
    return home(e) / "radar.db"


def geo_db_path(env: Optional[Mapping[str, str]] = None) -> Path:
    e = _env(env)
    if e.get("WACHIMAN_GEO_DB"):
        return _expand(e["WACHIMAN_GEO_DB"], e)
    return home(e) / "geo.db"


def config_path(env: Optional[Mapping[str, str]] = None) -> Path:
    return home(env) / "config.env"


def load_config(path: Path) -> Dict[str, str]:
    """Parse a KEY=VALUE file. Missing file -> empty dict. Never executes anything."""
    out: Dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def settings(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    e = _env(env)
    merged = dict(DEFAULTS)
    merged.update(load_config(config_path(e)))
    merged.update({k: v for k, v in e.items() if k.startswith("WACHIMAN_")})
    return merged
