"""Image files on disk, and the small JPEGs we send as references."""

from __future__ import annotations

import base64
import io
import uuid
from pathlib import Path

from PIL import Image

from koma.paths import images_dir


def sniff(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "png"


def save_image(data: bytes) -> Path:
    path = images_dir() / f"{uuid.uuid4().hex}.{sniff(data)}"
    path.write_bytes(data)
    return path


def media_name(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).name


def media_url(path: str | None) -> str | None:
    name = media_name(path)
    if not name:
        return None
    return f"koma:///media/{name}"


def flatten_on_white(image: Image.Image) -> Image.Image:
    """Composite onto white and return RGB, so clear pixels stay paper."""
    rgba = image.convert("RGBA")
    base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    base.alpha_composite(rgba)
    return base.convert("RGB")


def jpeg_data_uri(path: Path, longest: int = 768) -> str:
    """Shrink a reference to the size the image model actually uses."""
    with Image.open(path) as image:
        image = flatten_on_white(image)
        image.thumbnail((longest, longest), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def unlink(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink()
    except OSError:
        pass
