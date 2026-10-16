"""Finish sizes for a manga or a comic, and the pixels those sizes become.

These are trimmed page sizes, the book as a reader holds it. Manuscript
paper is larger than the finish size and is not what the picker offers.
"""

from __future__ import annotations

import math

MAX_EDGE = 5000
LEGACY_W = 1200
LEGACY_H = 1800

_KINDS = (
    {"id": "manga", "label": "Japanese manga", "reading": "rtl", "line": "Right to left"},
    {"id": "western", "label": "Western comics", "reading": "ltr", "line": "Left to right"},
)

# w and h are finish size in millimetres.
_PRESETS = (
    {"id": "shinsho", "kind": "manga", "name": "Shinsho", "note": "Shōnen and shōjo", "w": 105, "h": 173, "label": "105 × 173 mm"},
    {"id": "b6", "kind": "manga", "name": "B6", "note": "Seinen", "w": 128, "h": 182, "label": "128 × 182 mm"},
    {"id": "a5", "kind": "manga", "name": "A5", "note": "Wide and kanzenban", "w": 148, "h": 210, "label": "148 × 210 mm"},
    {"id": "b5", "kind": "manga", "name": "B5", "note": "Magazine", "w": 182, "h": 257, "label": "182 × 257 mm"},
    {"id": "us-issue", "kind": "western", "name": "US issue", "note": "Modern floppy", "w": 168.275, "h": 260.35, "label": "6.625 × 10.25 in"},
    {"id": "trade", "kind": "western", "name": "Trade", "note": "Paperback", "w": 152.4, "h": 228.6, "label": "6 × 9 in"},
    {"id": "digest", "kind": "western", "name": "Digest", "note": "Small book", "w": 139.7, "h": 215.9, "label": "5.5 × 8.5 in"},
    {"id": "album", "kind": "western", "name": "Album", "note": "European A4", "w": 210, "h": 297, "label": "210 × 297 mm"},
)

_QUALITIES = (
    {"id": "screen", "name": "Screen", "dpi": 144, "note": "On a display"},
    {"id": "print", "name": "Print", "dpi": 300, "note": "A finished book"},
    {"id": "press", "name": "Press", "dpi": 600, "note": "Fine line art"},
    {"id": "custom", "name": "Custom", "dpi": None, "note": "Your own"},
)

_DEFAULTS = {
    "manga": {"preset": "b6", "quality": "print"},
    "western": {"preset": "us-issue", "quality": "print"},
}

_LIMITS = {"dpiMin": 72, "dpiMax": 1200, "wMin": 40, "wMax": 500, "hMin": 40, "hMax": 700}


class FormatError(ValueError):
    """A page size the studio cannot draw."""


def catalog() -> dict:
    presets = []
    for row in _PRESETS:
        presets.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "name": row["name"],
                "note": row["note"],
                "wMm": row["w"],
                "hMm": row["h"],
                "label": row["label"],
            }
        )
    return {
        "maxEdge": MAX_EDGE,
        "kinds": [dict(item) for item in _KINDS],
        "presets": presets,
        "qualities": [dict(item) for item in _QUALITIES],
        "defaults": {key: dict(value) for key, value in _DEFAULTS.items()},
        "limits": dict(_LIMITS),
    }


def page_pixels(w_mm: float, h_mm: float, dpi: float, max_edge: int = MAX_EDGE) -> dict:
    """Pixel size of a finish page. The long edge stays within max_edge."""
    width = max(1, round(float(w_mm) / 25.4 * float(dpi)))
    height = max(1, round(float(h_mm) / 25.4 * float(dpi)))
    capped = False
    long_edge = max(width, height)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))
        if width > max_edge:
            width = max_edge
        if height > max_edge:
            height = max_edge
        capped = True
    return {"width": width, "height": height, "capped": capped}


def project_pixels(project: dict | None) -> tuple[int, int]:
    """Pixels for a stored project. Older rows with no trim stay 1200×1800."""
    project = project or {}
    try:
        dpi = int(project.get("page_dpi") or 0)
        width = float(project.get("trim_w_mm") or 0)
        height = float(project.get("trim_h_mm") or 0)
    except (TypeError, ValueError):
        return LEGACY_W, LEGACY_H
    if dpi <= 0 or width <= 0 or height <= 0:
        return LEGACY_W, LEGACY_H
    pixels = page_pixels(width, height, dpi)
    return pixels["width"], pixels["height"]


def resolve_format(
    kind: str,
    preset: str,
    quality: str,
    *,
    width=None,
    height=None,
    unit: str = "mm",
    dpi=None,
) -> dict:
    """Turn a picker choice into millimetres, dpi, reading direction, and pixels."""
    kind_id = str(kind or "")
    kind_row = next((item for item in _KINDS if item["id"] == kind_id), None)
    if kind_row is None:
        raise FormatError("Choose Japanese manga or Western comics.")

    quality_id = str(quality or "")
    quality_row = next((item for item in _QUALITIES if item["id"] == quality_id), None)
    if quality_row is None:
        raise FormatError("Choose a resolution.")
    if quality_id == "custom":
        dpi_value = _dpi(dpi)
    else:
        dpi_value = int(quality_row["dpi"])

    preset_id = str(preset or "")
    chosen_unit = "in" if str(unit or "").lower() == "in" else "mm"
    if preset_id == "custom":
        w_mm, h_mm = _custom_mm(width, height, chosen_unit)
    else:
        row = next((item for item in _PRESETS if item["id"] == preset_id and item["kind"] == kind_id), None)
        if row is None:
            raise FormatError("Choose a page size.")
        w_mm = float(row["w"])
        h_mm = float(row["h"])
        chosen_unit = "mm"

    pixels = page_pixels(w_mm, h_mm, dpi_value)
    return {
        "kind": kind_id,
        "preset": preset_id,
        "quality": quality_id,
        "reading": kind_row["reading"],
        "w_mm": w_mm,
        "h_mm": h_mm,
        "unit": chosen_unit,
        "dpi": dpi_value,
        "width": pixels["width"],
        "height": pixels["height"],
        "capped": pixels["capped"],
    }


def _custom_mm(width, height, unit: str) -> tuple[float, float]:
    w_value = _positive(width, "width")
    h_value = _positive(height, "height")
    factor = 25.4 if unit == "in" else 1.0
    w_mm = round(w_value * factor, 3)
    h_mm = round(h_value * factor, 3)
    if w_mm < _LIMITS["wMin"] or w_mm > _LIMITS["wMax"]:
        raise FormatError("Use a width between 40 and 500 mm.")
    if h_mm < _LIMITS["hMin"] or h_mm > _LIMITS["hMax"]:
        raise FormatError("Use a height between 40 and 700 mm.")
    return w_mm, h_mm


def _positive(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FormatError(f"Enter a {label}.") from None
    if not math.isfinite(number) or number <= 0:
        raise FormatError(f"Enter a {label}.")
    return number


def _dpi(value) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FormatError("Enter a resolution in dpi.") from None
    if not math.isfinite(number) or abs(number - round(number)) > 1e-6:
        raise FormatError("Use a whole number of dpi.")
    dpi = int(round(number))
    if dpi < _LIMITS["dpiMin"] or dpi > _LIMITS["dpiMax"]:
        raise FormatError("Use a resolution between 72 and 1200 dpi.")
    return dpi
