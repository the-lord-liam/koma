"""Local ink cleanup and region fills.

Pillow only. No network, no model, and no extra packages. A sketch becomes
black lines on a white ground. A lasso can soften just the marked part of a
panel, or paste a new drawing into that same part.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw, ImageFilter

from koma.lines import extract_lines


def load_png(raw: str) -> Image.Image:
    text = str(raw or "").strip()
    if "," in text and text.startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        data = base64.b64decode(text, validate=False)
    except Exception:
        raise ValueError("That sketch could not be read.") from None
    if not data or len(data) > 8_000_000:
        raise ValueError("That sketch is too large.")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise ValueError("That sketch could not be read.") from None
    if image.format != "PNG":
        image.close()
        raise ValueError("The sketch needs to be a PNG.")
    return image


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def clean_sketch(image: Image.Image) -> Image.Image:
    """Black lines on white. Color fills drop out; the strokes stay."""
    lines = extract_lines(image, 0.72)
    ground = Image.new("RGB", lines.size, (255, 255, 255))
    ground.paste((0, 0, 0), mask=lines.getchannel("A"))
    lines.close()
    return ground


def marks_of(raw, limit: int = 400) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        return []
    marks: list[tuple[float, float]] = []
    for item in raw[:limit]:
        if isinstance(item, dict):
            x, y = item.get("x"), item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = item[0], item[1]
        else:
            continue
        try:
            fx = float(x)
            fy = float(y)
        except (TypeError, ValueError):
            continue
        if fx != fx or fy != fy or fx in (float("inf"), float("-inf")) or fy in (float("inf"), float("-inf")):
            continue
        marks.append((min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))))
    return marks


def mask_from_marks(size: tuple[int, int], marks: list[tuple[float, float]], mode: str) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 0)
    if width < 1 or height < 1 or len(marks) < 2:
        return mask
    draw = ImageDraw.Draw(mask)
    if mode == "circle":
        ax, ay = marks[0]
        bx, by = marks[-1]
        box = [ax * width, ay * height, bx * width, by * height]
        box[0], box[2] = sorted((box[0], box[2]))
        box[1], box[3] = sorted((box[1], box[3]))
        if box[2] - box[0] < 2:
            box[2] = min(width, box[0] + 2)
        if box[3] - box[1] < 2:
            box[3] = min(height, box[1] + 2)
        draw.ellipse(box, fill=255)
        return mask
    if len(marks) < 3:
        return mask
    draw.polygon([(x * width, y * height) for x, y in marks], fill=255)
    return mask


def blur_region(image: Image.Image, mask: Image.Image, radius: int = 8) -> Image.Image:
    base = image.convert("RGB")
    soft = base.filter(ImageFilter.GaussianBlur(max(1, radius)))
    out = base.copy()
    out.paste(soft, mask=mask)
    return out


def paste_region(base: Image.Image, incoming: Image.Image, mask: Image.Image) -> Image.Image:
    ground = base.convert("RGB")
    fresh = incoming.convert("RGB")
    if fresh.size != ground.size:
        fresh = fresh.resize(ground.size, Image.Resampling.LANCZOS)
    out = ground.copy()
    out.paste(fresh, mask=mask)
    return out
