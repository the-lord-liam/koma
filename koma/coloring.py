"""Color line art and put the ink back on top.

The wash is chosen from a short note on this machine. Callers that use xAI
or a local draw server still finish through keep_lines, so the strokes stay.
"""

from __future__ import annotations

from PIL import Image

from koma.lines import extract_lines

_WASHES = (
    ("pink", (214, 120, 160)),
    ("orange", (214, 120, 48)),
    ("yellow", (214, 186, 72)),
    ("purple", (120, 80, 170)),
    ("brown", (120, 78, 48)),
    ("green", (64, 150, 96)),
    ("blue", (64, 112, 196)),
    ("red", (196, 64, 64)),
    ("night", (36, 48, 82)),
    ("warm", (214, 140, 96)),
    ("cool", (96, 140, 180)),
)


def wash_color(prompt: str) -> tuple[int, int, int]:
    text = str(prompt or "").lower()
    for word, color in _WASHES:
        if word in text:
            return color
    return (232, 214, 196)


def lines_on_white(image: Image.Image, strength: float = 0.72) -> tuple[Image.Image, Image.Image]:
    """Black lines on white, plus the ink mask used to restore them."""
    lines = extract_lines(image, strength)
    ground = Image.new("RGB", lines.size, (255, 255, 255))
    ground.paste((0, 0, 0), mask=lines.getchannel("A"))
    return ground, lines


def keep_lines(colored: Image.Image, lines: Image.Image) -> Image.Image:
    """Paint the original ink over a colored picture."""
    out = colored.convert("RGB")
    if out is colored:
        out = colored.copy()
    alpha = lines.getchannel("A")
    if out.size != lines.size:
        out = out.resize(lines.size, Image.Resampling.LANCZOS)
    if alpha.size != out.size:
        alpha = alpha.resize(out.size, Image.Resampling.NEAREST)
    ink = Image.new("RGB", out.size, (0, 0, 0))
    out.paste(ink, mask=alpha)
    return out


def color_offline(image: Image.Image, prompt: str) -> Image.Image:
    """Flat color behind the ink. No network."""
    ground, lines = lines_on_white(image)
    try:
        wash = Image.new("RGB", lines.size, wash_color(prompt))
        try:
            return keep_lines(wash, lines)
        finally:
            wash.close()
    finally:
        ground.close()
        lines.close()
