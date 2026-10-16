"""Local settings. The API key stays in a mode-600 file, or in XAI_API_KEY."""

from __future__ import annotations

import json
import os

from koma.paths import config_file

DEFAULTS = {
    "api_key": "",
    "quality": "low",
    "resolution": "1k",
}


def load_config() -> dict:
    data = dict(DEFAULTS)
    path = config_file()
    if path.exists():
        os.chmod(path, 0o600)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored and isinstance(stored[key], str):
                    data[key] = stored[key]
    env_key = os.environ.get("XAI_API_KEY", "").strip()
    if env_key and not data["api_key"]:
        data["api_key"] = env_key
    if data["quality"] not in {"low", "medium", "auto"}:
        data["quality"] = "low"
    if data["resolution"] not in {"1k", "2k"}:
        data["resolution"] = "1k"
    return data


def save_config(updates: dict) -> dict:
    current = load_config()
    # An env-only key is not written unless the user typed one.
    stored_key = _stored_key()
    current["api_key"] = stored_key
    if "api_key" in updates:
        current["api_key"] = str(updates["api_key"]).strip()
    if "quality" in updates and updates["quality"] in {"low", "medium", "auto"}:
        current["quality"] = updates["quality"]
    if "resolution" in updates and updates["resolution"] in {"1k", "2k"}:
        current["resolution"] = updates["resolution"]
    path = config_file()
    path.write_text(json.dumps({key: current[key] for key in DEFAULTS}, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return load_config()


def _stored_key() -> str:
    path = config_file()
    if not path.exists():
        return ""
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(stored, dict) and isinstance(stored.get("api_key"), str):
        return stored["api_key"]
    return ""


def public_settings(data: dict | None = None) -> dict:
    data = data or load_config()
    key = data.get("api_key") or ""
    return {
        "hasKey": bool(key),
        "keyHint": f"…{key[-4:]}" if len(key) >= 4 else "",
        "quality": data.get("quality") or "low",
        "resolution": data.get("resolution") or "1k",
    }
