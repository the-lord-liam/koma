"""Black line art from a picture already on this machine.

Sobel edges plus a darkness threshold, both in Pillow. No network, no model,
and no extra packages. Strength is 0 to 1: lower keeps only the darkest
strokes, higher also keeps lighter edges. Color fills stay clear except for
their outline. The ink is black and the ground is transparent.
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter, ImageOps

from koma.media import flatten_on_white


def strength_of(value, default: float = 0.55) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(0.0, min(1.0, number))


def extract_lines(image: Image.Image, strength: float = 0.55) -> Image.Image:
    """Return black ink on a transparent ground."""
    amount = strength_of(strength)
    rgb = flatten_on_white(image)
    # A red coat is dark in grayscale, but it is not ink. Near-gray darkness
    # stays; the edge pass draws the outline of a colored shape.
    neutral = _neutral_dark(rgb)
    edge = _sobel(ImageOps.grayscale(rgb))
    alpha = ImageChops.lighter(_ramp(neutral, *_dark_gate(amount)), _ramp(edge, *_edge_gate(amount)))
    out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
    out.putalpha(alpha)
    return out


def _neutral_dark(rgb: Image.Image) -> Image.Image:
    red, green, blue = rgb.split()
    high = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    low = ImageChops.darker(ImageChops.darker(red, green), blue)
    saturation = ImageChops.subtract(high, low)
    dark = ImageOps.invert(ImageOps.grayscale(rgb))
    return ImageChops.multiply(dark, ImageOps.invert(saturation))


def _sobel(gray: Image.Image) -> Image.Image:
    padded = _replicate(gray, 2)
    horizontal = ImageFilter.Kernel((3, 3), [-1, 0, 1, -2, 0, 2, -1, 0, 1], scale=8, offset=128)
    vertical = ImageFilter.Kernel((3, 3), [-1, -2, -1, 0, 0, 0, 1, 2, 1], scale=8, offset=128)
    mid = Image.new("L", padded.size, 128)
    edge = ImageChops.add(
        ImageChops.difference(padded.filter(horizontal), mid),
        ImageChops.difference(padded.filter(vertical), mid),
    )
    return edge.crop((2, 2, padded.width - 2, padded.height - 2))


def _replicate(image: Image.Image, n: int) -> Image.Image:
    width, height = image.size
    out = Image.new(image.mode, (width + 2 * n, height + 2 * n))
    out.paste(image, (n, n))
    for offset in range(n):
        out.paste(image.crop((0, 0, width, 1)), (n, offset))
        out.paste(image.crop((0, height - 1, width, height)), (n, height + n + offset))
    span = height + 2 * n
    for offset in range(n):
        out.paste(out.crop((n, 0, n + 1, span)), (offset, 0))
        out.paste(out.crop((n + width - 1, 0, n + width, span)), (n + width + offset, 0))
    return out


def _dark_gate(strength: float) -> tuple[int, int]:
    high = int(round(210 - strength * 120))
    return max(0, high - 18), high


def _edge_gate(strength: float) -> tuple[int, int]:
    high = int(round(70 - strength * 48))
    return max(0, high - 12), high


def _ramp(src: Image.Image, low: int, high: int) -> Image.Image:
    if high <= low:
        high = low + 1
    span = high - low
    table = []
    for value in range(256):
        if value <= low:
            table.append(0)
        elif value >= high:
            table.append(255)
        else:
            table.append((value - low) * 255 // span)
    return src.point(table)
