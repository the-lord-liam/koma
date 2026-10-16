"""Page layouts.

Slot rectangles are fractions of the page, origin at the top-left, in
left-to-right reading order. Right-to-left manga mirrors them so the first
slot stays the first one a reader meets.
"""

from __future__ import annotations

import math

# Visual width / height of a finished page.
PAGE_ASPECT = 2 / 3

LAYOUTS: dict[str, tuple[str, list[dict]]] = {
    "full": ("Full page", [{"x": 0, "y": 0, "w": 1, "h": 1}]),
    "classic": (
        "Large lead, two stacked",
        [
            {"x": 0, "y": 0, "w": 0.62, "h": 1},
            {"x": 0.62, "y": 0, "w": 0.38, "h": 0.5},
            {"x": 0.62, "y": 0.5, "w": 0.38, "h": 0.5},
        ],
    ),
    "v3": (
        "Three rows",
        [
            {"x": 0, "y": 0, "w": 1, "h": 1 / 3},
            {"x": 0, "y": 1 / 3, "w": 1, "h": 1 / 3},
            {"x": 0, "y": 2 / 3, "w": 1, "h": 1 / 3},
        ],
    ),
    "top-heavy": (
        "Wide top, two below",
        [
            {"x": 0, "y": 0, "w": 1, "h": 0.58},
            {"x": 0, "y": 0.58, "w": 0.5, "h": 0.42},
            {"x": 0.5, "y": 0.58, "w": 0.5, "h": 0.42},
        ],
    ),
    "bottom-heavy": (
        "Two above, wide bottom",
        [
            {"x": 0, "y": 0, "w": 0.5, "h": 0.42},
            {"x": 0.5, "y": 0, "w": 0.5, "h": 0.42},
            {"x": 0, "y": 0.42, "w": 1, "h": 0.58},
        ],
    ),
    "h2": (
        "Two columns",
        [
            {"x": 0, "y": 0, "w": 0.5, "h": 1},
            {"x": 0.5, "y": 0, "w": 0.5, "h": 1},
        ],
    ),
    "v2": (
        "Two rows",
        [
            {"x": 0, "y": 0, "w": 1, "h": 0.5},
            {"x": 0, "y": 0.5, "w": 1, "h": 0.5},
        ],
    ),
    "grid4": (
        "Four grid",
        [
            {"x": 0, "y": 0, "w": 0.5, "h": 0.5},
            {"x": 0.5, "y": 0, "w": 0.5, "h": 0.5},
            {"x": 0, "y": 0.5, "w": 0.5, "h": 0.5},
            {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5},
        ],
    ),
    "inset": (
        "Lead with three",
        [
            {"x": 0, "y": 0, "w": 0.58, "h": 0.62},
            {"x": 0.58, "y": 0, "w": 0.42, "h": 0.62},
            {"x": 0, "y": 0.62, "w": 0.5, "h": 0.38},
            {"x": 0.5, "y": 0.62, "w": 0.5, "h": 0.38},
        ],
    ),
    "grid6": (
        "Six grid",
        [
            {"x": 0, "y": 0, "w": 1 / 3, "h": 0.5},
            {"x": 1 / 3, "y": 0, "w": 1 / 3, "h": 0.5},
            {"x": 2 / 3, "y": 0, "w": 1 / 3, "h": 0.5},
            {"x": 0, "y": 0.5, "w": 1 / 3, "h": 0.5},
            {"x": 1 / 3, "y": 0.5, "w": 1 / 3, "h": 0.5},
            {"x": 2 / 3, "y": 0.5, "w": 1 / 3, "h": 0.5},
        ],
    ),
}

# (name, width/height). Log-distance keeps 2:1 as far from 1:1 as 1:2 is.
# Ratios the Imagine API accepts. Keep this list in sync with the docs.
_RATIOS = [
    ("1:1", 1.0),
    ("4:3", 4 / 3),
    ("3:2", 3 / 2),
    ("16:9", 16 / 9),
    ("2:1", 2.0),
    ("21:9", 21 / 9),
    ("5:2", 5 / 2),
    ("3:4", 3 / 4),
    ("2:3", 2 / 3),
    ("9:16", 9 / 16),
    ("1:2", 0.5),
]


def place(slots: list[dict], reading: str) -> list[dict]:
    """Return slot rects in on-page coordinates. RTL mirrors horizontally."""
    if reading != "rtl":
        return [dict(slot) for slot in slots]
    return [{**slot, "x": 1 - slot["x"] - slot["w"]} for slot in slots]


def slots_for(layout_id: str, reading: str = "ltr") -> list[dict]:
    _label, slots = LAYOUTS[layout_id]
    return place(slots, reading)


def aspect_for_slot(slot: dict, page_aspect: float | None = None) -> str:
    """Nearest Imagine aspect ratio for a slot.

    page_aspect is the page width divided by its height. The default is a 2:3 page.
    """
    aspect = PAGE_ASPECT if not page_aspect or page_aspect <= 0 else float(page_aspect)
    visual = (slot["w"] / slot["h"]) * aspect
    name, _ratio = min(_RATIOS, key=lambda item: abs(math.log(item[1]) - math.log(visual)))
    return name


class LayoutError(ValueError):
    """A cut that would leave a messy panel."""


def to_stored_line(x: float, y: float, angle: float, reading: str) -> tuple[float, float, float]:
    """Map a line from the page the creator sees into stored left-to-right slots."""
    if reading != "rtl":
        return float(x), float(y), float(angle)
    return 1 - float(x), float(y), -float(angle)


def _snap_axis(angle: float) -> str:
    folded = abs(float(angle)) % 180
    if min(folded, 180 - folded) <= abs(folded - 90):
        return "h"
    return "v"


def _axis_gap(angle: float, axis: str) -> float:
    folded = abs(float(angle)) % 180
    if axis == "h":
        return min(folded, 180 - folded)
    return abs(folded - 90)


def _round_slot(slot: dict) -> dict:
    return {axis: round(float(slot[axis]), 4) for axis in ("x", "y", "w", "h")}


def _slot_hit(slots: list[dict], x: float, y: float) -> int:
    for index, slot in enumerate(slots):
        if slot["x"] - 1e-6 <= x <= slot["x"] + slot["w"] + 1e-6 and slot["y"] - 1e-6 <= y <= slot["y"] + slot["h"] + 1e-6:
            return index
    best = 0
    best_distance = 1e9
    for index, slot in enumerate(slots):
        cx = slot["x"] + slot["w"] / 2
        cy = slot["y"] + slot["h"] / 2
        distance = (cx - x) ** 2 + (cy - y) ** 2
        if distance < best_distance:
            best = index
            best_distance = distance
    return best


def assist_divide(slots: list[dict], x: float, y: float, angle: float) -> tuple[list[dict], int, str, bool]:
    """Split one panel into two rectangles.

    The line may sit at any angle. The cut snaps to horizontal or vertical,
    and it is pulled into the middle band of the panel so neither piece is a sliver.
    """
    if not slots:
        raise LayoutError("This page has no panels to divide.")
    x = float(x)
    y = float(y)
    index = _slot_hit(slots, x, y)
    slot = slots[index]
    axis = _snap_axis(angle)
    if axis == "h":
        if slot["h"] < 0.16:
            raise LayoutError("That panel is too short to divide cleanly. Choose a taller panel.")
        local = (y - slot["y"]) / slot["h"]
        local = min(0.82, max(0.18, local))
        cut = slot["y"] + slot["h"] * local
        first = {**slot, "h": cut - slot["y"]}
        second = {**slot, "y": cut, "h": slot["y"] + slot["h"] - cut}
    else:
        if slot["w"] < 0.16:
            raise LayoutError("That panel is too narrow to divide cleanly. Choose a wider panel.")
        local = (x - slot["x"]) / slot["w"]
        local = min(0.82, max(0.18, local))
        cut = slot["x"] + slot["w"] * local
        first = {**slot, "w": cut - slot["x"]}
        second = {**slot, "x": cut, "w": slot["x"] + slot["w"] - cut}
    divided = []
    for item_index, item in enumerate(slots):
        if item_index == index:
            divided.append(_round_slot(first))
            divided.append(_round_slot(second))
        else:
            divided.append(_round_slot(item))
    return divided, index, axis, _axis_gap(angle, axis) > 8


def _overlaps(a: dict, b: dict) -> bool:
    left = max(a["x"], b["x"])
    top = max(a["y"], b["y"])
    right = min(a["x"] + a["w"], b["x"] + b["w"])
    bottom = min(a["y"] + a["h"], b["y"] + b["h"])
    return right - left > 1e-4 and bottom - top > 1e-4


def _clean_union(a: dict, b: dict) -> tuple[dict, float] | None:
    """Union of two rectangles that share a full edge. The length is that edge."""
    side_by_side = abs(a["y"] - b["y"]) < 1e-4 and abs(a["h"] - b["h"]) < 1e-4
    touch_x = abs((a["x"] + a["w"]) - b["x"]) < 1e-4 or abs((b["x"] + b["w"]) - a["x"]) < 1e-4
    if side_by_side and touch_x:
        union = {
            "x": min(a["x"], b["x"]),
            "y": a["y"],
            "w": a["w"] + b["w"],
            "h": a["h"],
        }
        return union, a["h"]
    stacked = abs(a["x"] - b["x"]) < 1e-4 and abs(a["w"] - b["w"]) < 1e-4
    touch_y = abs((a["y"] + a["h"]) - b["y"]) < 1e-4 or abs((b["y"] + b["h"]) - a["y"]) < 1e-4
    if stacked and touch_y:
        union = {
            "x": a["x"],
            "y": min(a["y"], b["y"]),
            "w": a["w"],
            "h": a["h"] + b["h"],
        }
        return union, a["w"]
    return None


def _equal_rows(count: int) -> list[dict]:
    slots = []
    top = 0.0
    for index in range(count):
        height = 1 / count
        if index == count - 1:
            height = 1 - top
        slots.append(_round_slot({"x": 0, "y": top, "w": 1, "h": height}))
        top += 1 / count
    return slots


def reflow_after_delete(slots: list[dict], index: int) -> list[dict]:
    """Fill the page after one frame is removed.

    A neighbor that shares a full edge grows into the gap. Otherwise the
    remaining frames are stacked in equal rows so nothing is left blank.
    """
    if len(slots) <= 1:
        return [{"x": 0, "y": 0, "w": 1, "h": 1}]
    removed = slots[index]
    rest = [dict(slot) for slot in slots[:index] + slots[index + 1 :]]
    if not rest:
        return [{"x": 0, "y": 0, "w": 1, "h": 1}]
    choice = None
    best = -1.0
    for position, slot in enumerate(rest):
        merged = _clean_union(slot, removed)
        if merged is None:
            continue
        union, length = merged
        if any(_overlaps(union, other) for other_index, other in enumerate(rest) if other_index != position):
            continue
        if length > best:
            best = length
            choice = (position, union)
    if choice is None:
        return _equal_rows(len(rest))
    position, union = choice
    rest[position] = _round_slot(union)
    return rest


def catalog() -> list[dict]:
    items = []
    for key, (label, slots) in LAYOUTS.items():
        items.append(
            {
                "id": key,
                "label": label,
                "count": len(slots),
                "slots": [
                    {axis: round(slot[axis], 5) for axis in ("x", "y", "w", "h")}
                    for slot in slots
                ],
            }
        )
    return items
