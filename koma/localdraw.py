"""Img2img against a draw server the user chose.

The request is posted only to that URL. The host must be this machine
(localhost, 127.0.0.1, or ::1). Redirects are refused. Nothing else is
contacted, and the request carries no telemetry.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import json
import socket
import urllib.request
from urllib.parse import urlparse

from PIL import Image

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_BODY_KEYS = ("init_images", "image", "prompt", "denoising_strength", "steps", "width", "height")
_MAX_BYTES = 12_000_000


class LocalDrawError(ValueError):
    pass


def loopback_url(url: str) -> str:
    """Return the URL when it stays on this machine. Otherwise raise."""
    text = str(url or "").strip()
    if not text or any(ch.isspace() for ch in text) or len(text) > 300:
        raise LocalDrawError("The local draw address has to stay on this machine.")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise LocalDrawError("The local draw address has to stay on this machine.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _LOOPBACK:
        raise LocalDrawError("The local draw address has to stay on this machine.")
    if host == "localhost" and not _localhost_is_loopback():
        raise LocalDrawError("The local draw address has to stay on this machine.")
    return text


def request_for(url: str, png: bytes, prompt: str, width: int, height: int) -> tuple[str, dict, bytes]:
    """Build one POST for the user-set URL. No other fields are added."""
    target = loopback_url(url)
    encoded = base64.b64encode(png).decode("ascii")
    text = str(prompt or "").strip()
    if not text:
        text = "manga panel, clean ink"
    if "no text" not in text.lower():
        text = f"{text}. No text, no letters, no watermark."
    payload = {
        "init_images": [encoded],
        "image": encoded,
        "prompt": text[:2000],
        "denoising_strength": 0.55,
        "steps": 20,
        "width": _edge(width),
        "height": _edge(height),
    }
    if tuple(payload) != _BODY_KEYS:
        raise LocalDrawError("The local draw address has to stay on this machine.")
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, image/png",
    }
    return target, headers, body


def img2img(
    url: str,
    png: bytes,
    prompt: str = "",
    *,
    width: int = 0,
    height: int = 0,
    transport=None,
    timeout: float = 30,
) -> bytes:
    target, headers, payload = request_for(url, png, prompt, width, height)
    if transport is None:
        raw, content_type = _post(target, headers, payload, timeout)
    else:
        raw, content_type = transport(target, payload, timeout)
    return parse_draw_response(raw, content_type)


def parse_draw_response(body: bytes, content_type: str) -> bytes:
    if not isinstance(body, (bytes, bytearray)):
        raise LocalDrawError("The local draw server did not return a picture.")
    data = bytes(body)
    if len(data) > _MAX_BYTES:
        raise LocalDrawError("The local draw server did not return a picture.")
    kind = str(content_type or "").split(";", 1)[0].strip().lower()
    if kind.startswith("image/") or data.startswith(b"\x89PNG"):
        return _as_png(data)
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LocalDrawError("The local draw server did not return a picture.") from None
    raw = _image_field(parsed)
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        decoded = base64.b64decode(raw, validate=False)
    except Exception:
        raise LocalDrawError("The local draw server did not return a picture.") from None
    return _as_png(decoded)


def _image_field(parsed) -> str:
    if not isinstance(parsed, dict):
        raise LocalDrawError("The local draw server did not return a picture.")
    images = parsed.get("images")
    if isinstance(images, list) and images and isinstance(images[0], str):
        return images[0]
    image = parsed.get("image")
    if isinstance(image, str) and image.strip():
        return image
    raise LocalDrawError("The local draw server did not return a picture.")


def _as_png(data: bytes) -> bytes:
    if not data or len(data) > _MAX_BYTES:
        raise LocalDrawError("The local draw server did not return a picture.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:
        raise LocalDrawError("The local draw server did not return a picture.") from None


def _edge(value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 512
    if number < 64:
        return 64
    if number > 1024:
        return 1024
    return number


def _localhost_is_loopback() -> bool:
    try:
        infos = socket.getaddrinfo("localhost", None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not address.is_loopback:
            return False
    return True


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalDrawError("The local draw address has to stay on this machine.")


def _post(url: str, headers: dict, payload: bytes, timeout: float) -> tuple[bytes, str]:
    # An empty proxy map ignores http_proxy, so the sketch is not sent onward.
    opener = urllib.request.build_opener(_RefuseRedirect, urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            final = response.geturl()
            if urlparse(final).hostname != urlparse(url).hostname:
                raise LocalDrawError("The local draw address has to stay on this machine.")
            body = response.read(_MAX_BYTES + 1)
            if len(body) > _MAX_BYTES:
                raise LocalDrawError("The local draw server did not return a picture.")
            return body, response.headers.get("Content-Type", "")
    except LocalDrawError:
        raise
    except Exception:
        raise LocalDrawError("The local draw server did not return a picture.") from None
