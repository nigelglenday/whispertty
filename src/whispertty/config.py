"""Read and write whispertty's settings TOML."""

import os
import tomllib
from pathlib import Path

import tomli_w

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "whispertty" / "config.toml"

DEFAULTS: dict = {
    "transcripts_dir": str(Path.home() / "Documents" / "transcripts"),
    "whisper_model": "base",
    "default_recording": "mic",  # "mic" | "system"
    "auto_open_after_stop": False,
    "auto_copy_on_stop": True,  # land transcript text on clipboard after stop
    "keep_audio": True,
    "output_format": "txt",  # "txt" | "md"
}

# Keys that store boolean values (used to coerce strings from CLI input).
_BOOL_KEYS = {"auto_open_after_stop", "auto_copy_on_stop", "keep_audio"}


def config_path() -> Path:
    """Return the active config file path, honoring $WHISPERTTY_CONFIG."""
    env = os.environ.get("WHISPERTTY_CONFIG")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_CONFIG_PATH


def _ensure_exists() -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        with p.open("wb") as f:
            tomli_w.dump({"settings": DEFAULTS}, f)


def _load_raw() -> dict:
    _ensure_exists()
    with config_path().open("rb") as f:
        return tomllib.load(f)


def _save_raw(data: dict) -> None:
    _ensure_exists()
    with config_path().open("wb") as f:
        tomli_w.dump(data, f)


def load_settings() -> dict:
    """Return settings merged with DEFAULTS so missing keys are filled in."""
    raw = _load_raw().get("settings", {})
    merged = dict(DEFAULTS)
    merged.update(raw)
    return merged


def get(key: str):
    """Read a single setting (with default fallback)."""
    return load_settings().get(key)


def set_value(key: str, value) -> None:
    """Set or update a setting and persist."""
    if key in _BOOL_KEYS and isinstance(value, str):
        value = value.lower() in ("1", "true", "yes", "on")
    data = _load_raw()
    settings = data.setdefault("settings", {})
    settings[key] = value
    _save_raw(data)


def transcripts_dir() -> Path:
    return Path(get("transcripts_dir")).expanduser()
