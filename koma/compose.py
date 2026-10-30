"""Assemble a print page.

Panel art stays borderless. Gutters, borders, balloons, sound effects, and
the page number are drawn here, so the lettering is the author's text.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from koma.layouts import place, slots_for
from koma.media import flatten_on_white

PAGE_W = 1200
PAGE_H = 1800
MARGIN = 42
GUTTER = 16
BORDER = 5

# Noto CJK is the intended face. The other paths are common on Ubuntu when
# that package is not installed. DejaVu cannot draw kana, so it is last.
_CJK_BOLD = (
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
)
_CJK_REGULAR = (
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
)
_FALLBACK = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_FALLBACK_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _first_font(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _first_font(_CJK_BOLD) if bold else None
    if path is None:
        path = _first_font(_CJK_REGULAR)
    if path is None:
        path = _FALLBACK_BOLD if bold else _FALLBACK
        if not path.is_file():
            path = _FALLBACK if _FALLBACK.is_file() else None
    if path is not None:
        return ImageFont.truetype(str(path), size=size, index=0)
    return ImageFont.load_default()


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1100 <= code <= 0x11FF
        or 0x2E80 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x3FFFF
    )


def _cjk_candidates(bold: bool) -> list[Path]:
    found: list[Path] = []
    paths = (*_CJK_BOLD, *_CJK_REGULAR) if bold else _CJK_REGULAR
    for path in paths:
        if path.is_file() and path not in found:
            found.append(path)
    return found


@lru_cache(maxsize=64)
def _covers(path: str, sample: str) -> bool:
    try:
        face = ImageFont.truetype(path, size=48, index=0)
    except OSError:
        return False
    boxes = [face.getbbox(ch) for ch in sample]
    return len(set(boxes)) == len(sample)


def _open(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size, index=0)


def letter_font(size: int, bold: bool, text: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A face that can draw this string.

    Droid Sans Fallback has kana but draws Latin as the missing-glyph box.
    Prefer a CJK face that also has Latin (Noto, then WenQuanYi). Latin-only
    text falls back to DejaVu when every CJK face lacks those letters.
    """
    need_cjk = any(_is_cjk(ch) for ch in text)
    need_latin = any(ch.isascii() and not ch.isspace() for ch in text)
    if need_cjk and need_latin:
        for path in _cjk_candidates(bold):
            if _covers(str(path), "KA") and _covers(str(path), "あい"):
                return _open(path, size)
        return font(size, bold=bold)
    if need_cjk or not need_latin:
        return font(size, bold=bold)
    for path in _cjk_candidates(bold):
        if _covers(str(path), "KA"):
            return _open(path, size)
    latin = _FALLBACK_BOLD if bold and _FALLBACK_BOLD.is_file() else _FALLBACK
    if latin.is_file():
        return _open(latin, size)
    return font(size, bold=bold)


def _s(value: float, scale: float, minimum: int = 1) -> int:
    """A pixel size. Scale 1 keeps the original integer."""
    if scale == 1.0:
        return int(value)
    return max(minimum, round(value * scale))


def _page_frame(width: int | None, height: int | None) -> tuple[int, int, int, int, int, float]:
    page_w = PAGE_W if width is None else int(width)
    page_h = PAGE_H if height is None else int(height)
    if page_w == PAGE_W and page_h == PAGE_H:
        return page_w, page_h, MARGIN, GUTTER, BORDER, 1.0
    scale = page_h / PAGE_H
    margin = max(8, round(MARGIN * scale))
    gutter = max(4, round(GUTTER * scale))
    margin = min(margin, max(4, page_w // 10))
    gutter = min(gutter, max(2, margin))
    border = max(1, round(BORDER * scale))
    return page_w, page_h, margin, gutter, border, scale


def panel_boxes(
    layout_id: str,
    reading: str,
    width: int | None = None,
    height: int | None = None,
    slots: list[dict] | None = None,
) -> list[tuple[int, int, int, int]]:
    page_w, page_h, margin, gutter, _border, _scale = _page_frame(width, height)
    slots = slots_for(layout_id, reading) if slots is None else place(slots, reading)
    inner_w = page_w - margin * 2
    inner_h = page_h - margin * 2
    boxes = []
    for slot in slots:
        x = margin + slot["x"] * inner_w
        y = margin + slot["y"] * inner_h
        w = slot["w"] * inner_w
        h = slot["h"] * inner_h
        boxes.append(
            (
                round(x + gutter / 2),
                round(y + gutter / 2),
                max(8, round(w - gutter)),
                max(8, round(h - gutter)),
            )
        )
    return boxes


def render_page(
    layout_id: str,
    reading: str,
    panels: list[dict],
    page_label: str,
    out_path: Path,
    width: int | None = None,
    height: int | None = None,
    slots: list[dict] | None = None,
) -> Path:
    page_w, page_h, _margin, _gutter, border, scale = _page_frame(width, height)
    page = Image.new("RGB", (page_w, page_h), "white")
    draw = ImageDraw.Draw(page)
    boxes = panel_boxes(layout_id, reading, page_w, page_h, slots)
    by_slot = {int(panel["slot"]): panel for panel in panels}
    label_font = letter_font(_s(22, scale, 10), False, "Panel")
    for index, box in enumerate(boxes):
        panel = by_slot.get(index, {})
        _paste_art(page, box, panel.get("image_path"), index + 1, label_font)
        draw.rectangle(
            [box[0], box[1], box[0] + box[2] - 1, box[1] + box[3] - 1],
            outline="black",
            width=border,
        )
        _letter(
            draw,
            box,
            panel.get("dialogue") or "",
            panel.get("balloon") or "round",
            panel.get("balloon_anchor") or "tr",
            scale,
        )
        _sfx(draw, box, panel.get("sfx") or "", panel.get("balloon_anchor") or "tr", scale)
    number_font = letter_font(_s(20, scale, 10), False, page_label)
    draw.text((page_w / 2, page_h - _s(22, scale, 8)), page_label, font=number_font, fill="#222222", anchor="mm")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page.save(out_path, "PNG")
    return out_path


def _paste_art(page: Image.Image, box, path: str | None, number: int, label_font) -> None:
    x, y, w, h = box
    if path and Path(path).exists():
        try:
            with Image.open(path) as art:
                art = flatten_on_white(art)
                scale = max(w / art.width, h / art.height)
                resized = art.resize(
                    (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
                    Image.Resampling.LANCZOS,
                )
                left = max(0, (resized.width - w) // 2)
                top = max(0, (resized.height - h) // 2)
                crop = resized.crop((left, top, left + w, top + h))
                page.paste(crop, (x, y))
            return
        except OSError:
            pass
    draw = ImageDraw.Draw(page)
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill="#f3f3f3")
    draw.text((x + w / 2, y + h / 2), f"Panel {number}", font=label_font, fill="#b0b0b0", anchor="mm")


def _letter(draw: ImageDraw.ImageDraw, box, text: str, kind: str, anchor: str, scale: float = 1.0) -> None:
    text = text.strip()
    if not text or kind == "none":
        return
    x, y, w, h = box
    size = _s(28 if len(text) < 80 else 22, scale, 8)
    face = letter_font(size, False, text)
    max_w = max(_s(40, scale, 8), int(w * 0.7) - _s(28, scale, 4))
    lines = _wrap(draw, text, face, max_w)
    line_h = size + _s(6, scale, 2)
    floor = _s(16, scale, 8)
    while len(lines) * line_h > h * 0.55 and size > floor:
        size -= 2 if scale == 1.0 else max(1, round(2 * scale))
        face = letter_font(size, False, text)
        line_h = size + _s(6, scale, 2)
        lines = _wrap(draw, text, face, max_w)
    text_w = max((_length(draw, line, face) for line in lines), default=0)
    bw = min(w - _s(16, scale, 4), int(text_w) + _s(28, scale, 8))
    bh = min(h - _s(16, scale, 4), line_h * len(lines) + _s(20, scale, 6))
    pad = _s(10, scale, 2)
    if anchor == "tl":
        bx, by = x + pad, y + pad
    elif anchor == "bl":
        bx, by = x + pad, y + h - bh - pad
    elif anchor == "br":
        bx, by = x + w - bw - pad, y + h - bh - pad
    else:
        bx, by = x + w - bw - pad, y + pad
    rect = (bx, by, bx + bw, by + bh)
    stroke = _s(3, scale, 1)
    if kind == "caption":
        draw.rectangle(rect, fill="white", outline="black", width=stroke)
    elif kind == "shout":
        _burst(draw, rect, scale)
    else:
        draw.ellipse(rect, fill="white", outline="black", width=stroke)
        if kind == "thought":
            cx = bx + bw / 2
            cy = by + bh if anchor.startswith("t") else by
            direction = 1 if anchor.startswith("t") else -1
            hair = _s(2, scale, 1)
            draw.ellipse(
                [cx - _s(8, scale, 2), cy + direction * _s(6, scale, 1), cx + _s(4, scale, 1), cy + direction * _s(18, scale, 3)],
                fill="white",
                outline="black",
                width=hair,
            )
            draw.ellipse(
                [cx - _s(16, scale, 3), cy + direction * _s(18, scale, 3), cx - _s(6, scale, 1), cy + direction * _s(28, scale, 4)],
                fill="white",
                outline="black",
                width=hair,
            )
        else:
            _tail(draw, rect, x, y, w, h, anchor, scale)
    ty = by + (bh - line_h * len(lines)) / 2
    for line in lines:
        draw.text((bx + bw / 2, ty), line, font=face, fill="black", anchor="mt")
        ty += line_h


def _tail(draw, rect, x, y, w, h, anchor: str, scale: float = 1.0) -> None:
    bx, by, br, bb = rect
    reach = _s(22, scale, 4)
    inset = _s(4, scale, 1)
    left = _s(16, scale, 2)
    right = _s(36, scale, 4)
    stroke = _s(3, scale, 1)
    lift = 2 if scale == 1.0 else max(1, round(2 * scale))
    if anchor.startswith("t"):
        base_y = bb - lift
        tip = (bx + (br - bx) * 0.35, base_y + min(reach, (y + h) - bb - inset))
        draw.polygon([(bx + left, base_y), (bx + right, base_y), tip], fill="white")
        draw.line([(bx + left, base_y), tip, (bx + right, base_y)], fill="black", width=stroke)
    else:
        base_y = by + lift
        tip = (bx + (br - bx) * 0.35, base_y - min(reach, by - y - inset))
        draw.polygon([(bx + left, base_y), (bx + right, base_y), tip], fill="white")
        draw.line([(bx + left, base_y), tip, (bx + right, base_y)], fill="black", width=stroke)


def _burst(draw, rect, scale: float = 1.0) -> None:
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    points = []
    spikes = 16
    for i in range(spikes):
        angle = -math.pi / 2 + i * (2 * math.pi / spikes)
        spike = 1.18 if i % 2 == 0 else 0.84
        points.append((cx + math.cos(angle) * rx * spike, cy + math.sin(angle) * ry * spike))
    draw.polygon(points, fill="white")
    draw.line(points + [points[0]], fill="black", width=_s(3, scale, 1))


def _sfx(draw, box, text: str, balloon_anchor: str, scale: float = 1.0) -> None:
    text = text.strip()
    if not text:
        return
    x, y, w, h = box
    size = max(_s(28, scale, 10), min(_s(72, scale, 16), int(h * 0.16)))
    floor = _s(16, scale, 8)
    face = letter_font(size, True, text)
    limit = w * 0.9
    while size > floor and draw.textlength(text, font=face) > limit:
        size -= 2
        face = letter_font(size, True, text)
    if balloon_anchor.startswith("t"):
        pos = (x + w * 0.5, y + h * 0.78)
    else:
        pos = (x + w * 0.5, y + h * 0.28)
    _stroke(draw, pos, text, face, scale)


def _stroke(draw, pos, text: str, face, scale: float = 1.0) -> None:
    x, y = pos
    if scale == 1.0:
        radius, limit = 3, 10
    else:
        radius = max(1, round(3 * scale))
        limit = radius * radius + radius
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx * dx + dy * dy <= limit:
                draw.text((x + dx, y + dy), text, font=face, fill="white", anchor="mm")
    draw.text((x, y), text, font=face, fill="black", anchor="mm")


def _wrap(draw, text: str, face, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        tokens = para.split(" ") if " " in para else list(para)
        joiner = " " if " " in para else ""
        current = ""
        for token in tokens:
            trial = token if not current else current + joiner + token
            if _length(draw, trial, face) <= max_w:
                current = trial
                continue
            if current:
                lines.append(current)
            if _length(draw, token, face) <= max_w:
                current = token
                continue
            piece = ""
            for char in token:
                if _length(draw, piece + char, face) <= max_w:
                    piece += char
                else:
                    if piece:
                        lines.append(piece)
                    piece = char
            current = piece
        if current:
            lines.append(current)
    return lines or [""]


def _length(draw, text: str, face) -> float:
    return draw.textlength(text, font=face)
