"""xAI API client. Text uses the Responses API. Images use Imagine.

The key is read at call time. It is never written into errors or logs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable

from koma.media import jpeg_data_uri

API = "https://api.x.ai/v1"
TEXT_MODEL = "grok-4.7"
IMAGE_MODEL = "grok-imagine-image-2.0"


class AIError(Exception):
    pass


class Client:
    def __init__(self, key_getter: Callable[[], str]):
        self._key_getter = key_getter

    def _key(self) -> str:
        key = (self._key_getter() or "").strip()
        if not key:
            raise AIError("Add your xAI API key in Settings. Get one at console.x.ai.")
        return key

    def _redact(self, text: str) -> str:
        key = (self._key_getter() or "").strip()
        if key and key in text:
            return text.replace(key, "…")
        return text

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        request = urllib.request.Request(
            API + path,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            message = raw
            try:
                payload = json.loads(raw)
                message = (payload.get("error") or {}).get("message") or raw
            except json.JSONDecodeError:
                pass
            raise AIError(self._redact(f"xAI API ({error.code}): {message}")) from None
        except urllib.error.URLError as error:
            raise AIError(f"Could not reach api.x.ai ({error.reason}).") from None
        except TimeoutError:
            raise AIError("The xAI request timed out. Try again.") from None

    def complete(self, messages: list[dict], model: str = TEXT_MODEL) -> str:
        # store false keeps the manuscript off the server.
        data = self._post(
            "/responses",
            {"model": model, "store": False, "input": messages},
            timeout=180,
        )
        return _output_text(data)

    def imagine(
        self,
        prompt: str,
        aspect: str,
        refs: list[str],
        *,
        resolution: str,
        model: str = IMAGE_MODEL,
    ) -> bytes:
        path, body = image_request(
            prompt,
            aspect,
            refs,
            model=model,
            resolution=resolution,
        )
        data = self._post(path, body, timeout=180)
        return _image_bytes(data)


def image_request(
    prompt: str,
    aspect: str,
    refs: list[str],
    *,
    model: str,
    resolution: str,
) -> tuple[str, dict]:
    body: dict = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect,
        "response_format": "b64_json",
        "resolution": resolution,
    }
    if refs:
        body["images"] = [
            {"url": jpeg_data_uri(path), "type": "image_url"} for path in refs[:5]
        ]
        return "/images/edits", body
    return "/images/generations", body


def _output_text(data: dict) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for chunk in item.get("content") or []:
            if isinstance(chunk, dict) and chunk.get("type") in {"output_text", "text"}:
                parts.append(chunk.get("text") or "")
            elif isinstance(chunk, str):
                parts.append(chunk)
    text = "\n".join(part for part in parts if part).strip()
    if text:
        return text
    raise AIError("The model returned no text.")


def _image_bytes(data: dict) -> bytes:
    import base64
    import urllib.request

    items = data.get("data") or []
    if not items:
        raise AIError("The image model returned no image.")
    item = items[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    url = item.get("url")
    if url:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    raise AIError("The image model returned no image data.")
