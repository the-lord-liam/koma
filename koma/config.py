"""Local settings. The API key stays in a mode-600 file, or in XAI_API_KEY."""

from __future__ import annotations

import json
import os

from koma.paths import config_file

DEFAULTS = {
    "api_key": "",
    "quality": "low",
    "resolution": "1k",
    "mesh_provider": "local",
    "meshy_key": "",
    "tripo_key": "",
    "mesh_command": "",
}

_SECRET_KEYS = ("api_key", "meshy_key", "tripo_key")
_ENV_KEYS = {
    "api_key": "XAI_API_KEY",
    "meshy_key": "MESHY_API_KEY",
    "tripo_key": "TRIPO_API_KEY",
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
    for key, env_name in _ENV_KEYS.items():
        env_value = os.environ.get(env_name, "").strip()
        if env_value and not data[key]:
            data[key] = env_value
    if data["quality"] not in {"low", "medium", "auto"}:
        data["quality"] = "low"
    if data["resolution"] not in {"1k", "2k"}:
        data["resolution"] = "1k"
    if data["mesh_provider"] not in {"local", "meshy", "tripo"}:
        data["mesh_provider"] = "local"
    return data


def save_config(updates: dict) -> dict:
    current = load_config()
    # An env-only key is not written unless the user typed one.
    for key in _SECRET_KEYS:
        current[key] = _stored_value(key)
    if "api_key" in updates:
        current["api_key"] = str(updates["api_key"]).strip()
    if "meshy_key" in updates:
        current["meshy_key"] = str(updates["meshy_key"]).strip()
    if "tripo_key" in updates:
        current["tripo_key"] = str(updates["tripo_key"]).strip()
    if "quality" in updates and updates["quality"] in {"low", "medium", "auto"}:
        current["quality"] = updates["quality"]
    if "resolution" in updates and updates["resolution"] in {"1k", "2k"}:
        current["resolution"] = updates["resolution"]
    if "mesh_provider" in updates and updates["mesh_provider"] in {"local", "meshy", "tripo"}:
        current["mesh_provider"] = updates["mesh_provider"]
    if "mesh_command" in updates:
        current["mesh_command"] = str(updates["mesh_command"]).strip()[:500]
    path = config_file()
    path.write_text(json.dumps({key: current[key] for key in DEFAULTS}, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return load_config()


def _stored_value(key: str) -> str:
    path = config_file()
    if not path.exists():
        return ""
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(stored, dict) and isinstance(stored.get(key), str):
        return stored[key]
    return ""


def public_settings(data: dict | None = None) -> dict:
    from koma.mesh import command_available

    data = data or load_config()
    key = data.get("api_key") or ""
    meshy = data.get("meshy_key") or ""
    tripo = data.get("tripo_key") or ""
    provider = data.get("mesh_provider") or "local"
    if provider not in {"local", "meshy", "tripo"}:
        provider = "local"
    return {
        "hasKey": bool(key),
        "keyHint": f"…{key[-4:]}" if len(key) >= 4 else "",
        "quality": data.get("quality") or "low",
        "resolution": data.get("resolution") or "1k",
        "meshProvider": provider,
        "hasMeshyKey": bool(meshy),
        "meshyHint": f"…{meshy[-4:]}" if len(meshy) >= 4 else "",
        "hasTripoKey": bool(tripo),
        "tripoHint": f"…{tripo[-4:]}" if len(tripo) >= 4 else "",
        "meshCommand": data.get("mesh_command") or "",
        "localReady": command_available(data),
    }
