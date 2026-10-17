"""Filesystem locations for Koma. Projects stay on this machine."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"

APP_NAME = "koma"


def config_dir() -> Path:
    override = os.environ.get("KOMA_CONFIG_DIR")
    path = Path(override) if override else Path.home() / ".config" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def config_file() -> Path:
    return config_dir() / "config.json"


def data_dir() -> Path:
    override = os.environ.get("KOMA_DATA_DIR")
    path = Path(override) if override else Path.home() / ".local" / "share" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "koma.sqlite"


def images_dir() -> Path:
    path = data_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def previews_dir() -> Path:
    path = data_dir() / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def meshes_dir() -> Path:
    path = data_dir() / "meshes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_slug(text: str) -> str:
    """Folder-safe name. Keeps letters from any language so two books do not share one export folder."""
    cleaned = text.strip().replace("/", " ").replace("\\", " ")
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
    slug = re.sub(r"[^\w.\-]+", "-", cleaned, flags=re.UNICODE).strip("-._")
    if not slug or slug in {".", ".."}:
        return "manga"
    return slug[:60]


def export_root() -> Path:
    override = os.environ.get("KOMA_EXPORT_DIR")
    path = Path(override) if override else Path.home() / "Documents" / "Koma"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir(project_title: str) -> Path:
    path = export_root() / safe_slug(project_title)
    path.mkdir(parents=True, exist_ok=True)
    return path
