"""Dot, line, and gradient screentone.

Pillow only. No network and no extra packages. The pattern multiplies onto
the picture, so black ink stays black. A mask limits the tone to one area.
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageOps

from koma.lines import strength_of
from koma.media import flatten_on_white


def tone_kind(value) -> str:
    kind = str(value or "").strip().lower()
    if kind in {"dot", "line", "gradient"}:
        return kind
    return "dot"


def apply_tone(
    image: Image.Image,
    kind: str = "dot",
    density: float = 0.45,
    size: float = 0.35,
    mask: Image.Image | None = None,
) -> Image.Image:
    """Return a new picture with tone laid over it."""
    base = flatten_on_white(image)
    width, height = base.size
    if width < 1 or height < 1:
        return base
    pattern = tone_kind(kind)
    amount = strength_of(density, 0.45)
    scale = strength_of(size, 0.35)
    ink = _ink(width, height, pattern, amount, scale)
    paper = ImageOps.invert(ink)
    shade = Image.merge("RGB", (paper, paper, paper))
    toned = ImageChops.multiply(base, shade)
    paper.close()
    shade.close()
    ink.close()
    if mask is None:
        base.close()
        return toned
    gate = mask.convert("L")
    if gate.size != toned.size:
        resized = gate.resize(toned.size, Image.Resampling.NEAREST)
        gate.close()
        gate = resized
    if gate.getbbox() is None:
        gate.close()
        toned.close()
        return base
    merged = base.copy()
    base.close()
    merged.paste(toned, mask=gate)
    gate.close()
    toned.close()
    return merged


def _ink(width: int, height: int, kind: str, density: float, size: float) -> Image.Image:
    cell = max(4, int(round(4 + size * 20)))
    if kind == "line":
        return _lines(width, height, density, cell)
    if kind == "gradient":
        return _gradient(width, height, density, size)
    return _dots(width, height, density, cell)


def _dots(width: int, height: int, density: float, cell: int) -> Image.Image:
    ink = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(ink)
    radius = max(1, int(round(cell * (0.08 + density * 0.36))))
    y = cell // 2
    while y < height:
        x = cell // 2
        while x < width:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
            x += cell
        y += cell
    return ink


def _lines(width: int, height: int, density: float, cell: int) -> Image.Image:
    ink = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(ink)
    thickness = max(1, int(round(cell * (0.08 + density * 0.4))))
    y = 0
    while y < height:
        draw.rectangle((0, y, width, min(height, y + thickness)), fill=255)
        y += cell
    return ink


def _gradient(width: int, height: int, density: float, size: float) -> Image.Image:
    span = max(1, int(round(height * (0.25 + 0.75 * size))))
    ramp = Image.linear_gradient("L").resize((width, span), Image.Resampling.BILINEAR)
    peak = 40 + density * 200
    ramp = ramp.point(lambda value: int(value / 255 * peak))
    ink = Image.new("L", (width, height), 0)
    ink.paste(ramp, (0, height - span))
    ramp.close()
    return ink
