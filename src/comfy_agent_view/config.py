from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass(frozen=True)
class AppConfig:
    comfyui_user_dir: str | None = None
    default_profile: str = "safe"
    allow_full_profile: bool = True


def load_config() -> AppConfig:
    path = _config_path()
    if not path.exists():
        return AppConfig()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    section = data.get("comfy_agent_view", {})
    if not isinstance(section, dict):
        return AppConfig()
    return AppConfig(
        comfyui_user_dir=_optional_string(section.get("comfyui_user_dir")),
        default_profile=str(section.get("default_profile") or "safe"),
        allow_full_profile=bool(section.get("allow_full_profile", True)),
    )


def config_path() -> Path:
    return _config_path()


def _config_path() -> Path:
    override = os.environ.get("COMFY_AGENT_VIEW_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "comfy-agent-view" / "config.toml"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "comfy-agent-view" / "config.toml"

    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "comfy-agent-view" / "config.toml"


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
