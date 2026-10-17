"""3D models for a character.

The default backend is local. It lifts one picture into a mesh and does not
call a paid API. Text-to-3D draws that picture first (an existing portrait, or
an xAI image) and then lifts it. Meshy and Tripo are optional and use a key
the user saves in Settings.

Model weights are never shipped with Koma. A missing local install raises
MeshError with setup steps instead of failing quietly.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from koma.paths import ROOT, data_dir

PROVIDERS = ("local", "meshy", "tripo")
MESHY_API = "https://api.meshy.ai"
TRIPO_API = "https://api.tripo3d.ai"
_POLLS = 40

NOT_INSTALLED = (
    "The local 3D backend is not installed. "
    "Koma's default is TripoSR, which turns one picture into a mesh on your machine. "
    "A GPU with about 6 GB free is comfortable; CPU works and is slow. "
    "Model weights are not included. "
    "Clone https://github.com/VAST-AI-Research/TripoSR into "
    "~/.local/share/koma/backends/triposr and install its Python requirements. "
    "Koma will use that checkout. You can also set a Local 3D command in Settings, "
    "or KOMA_MESH_CMD, to a program that accepts `image.png --output model.glb`. "
    "See extras/mesh/README.md."
)


class MeshError(ValueError):
    """The model could not be built. Shown in the studio."""


def cube_glb() -> bytes:
    """A small cube. Used by tests and the viewer screenshot, not as a character."""
    size = 0.6
    vertices = [
        (-size, -size, -size),
        (size, -size, -size),
        (size, size, -size),
        (-size, size, -size),
        (-size, -size, size),
        (size, -size, size),
        (size, size, size),
        (-size, size, size),
    ]
    faces = [
        (4, 5, 6),
        (4, 6, 7),
        (1, 0, 3),
        (1, 3, 2),
        (3, 7, 6),
        (3, 6, 2),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (0, 4, 7),
        (0, 7, 3),
    ]
    indices = [index for face in faces for index in face]
    return pack_glb(vertices, indices)


def pack_glb(vertices: list[tuple[float, float, float]], indices: list[int]) -> bytes:
    if len(vertices) < 3 or len(indices) < 3:
        raise MeshError("That model has no faces.")
    positions = b"".join(struct.pack("<3f", x, y, z) for x, y, z in vertices)
    wide = any(index > 65535 for index in indices)
    if wide:
        index_bytes = b"".join(struct.pack("<I", index) for index in indices)
        component = 5125
    else:
        index_bytes = b"".join(struct.pack("<H", index) for index in indices)
        component = 5123
    blob = positions + index_bytes
    pad = (4 - (len(blob) % 4)) % 4
    blob += b"\x00" * pad
    xs = [vert[0] for vert in vertices]
    ys = [vert[1] for vert in vertices]
    zs = [vert[2] for vert in vertices]
    document = {
        "asset": {"version": "2.0", "generator": "koma"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4},
                ]
            }
        ],
        "buffers": [{"byteLength": len(positions) + len(index_bytes)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(index_bytes), "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(vertices),
                "type": "VEC3",
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            },
            {
                "bufferView": 1,
                "componentType": component,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
    }
    return _assemble(document, blob)


def parse_glb(data: bytes) -> tuple[list[tuple[float, float, float]], list[int]]:
    document, blob = _chunks(data)
    positions: list[tuple[float, float, float]] = []
    indices: list[int] = []
    for mesh in document.get("meshes") or []:
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            mode = primitive.get("mode", 4)
            if mode not in (4, None):
                continue
            attributes = primitive.get("attributes") or {}
            if "POSITION" not in attributes:
                continue
            chunk = _read_vec3(document, blob, attributes["POSITION"])
            if "indices" in primitive:
                local = _read_scalar(document, blob, primitive["indices"])
            else:
                local = list(range(len(chunk)))
            base = len(positions)
            positions.extend(chunk)
            indices.extend(index + base for index in local)
    if len(positions) < 3 or len(indices) < 3:
        raise MeshError("That model has no triangle mesh Koma can read.")
    return positions, indices


def obj_from_glb(data: bytes) -> bytes:
    positions, indices = parse_glb(data)
    lines = ["# Koma"]
    for x, y, z in positions:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for offset in range(0, len(indices) - 2, 3):
        a, b, c = indices[offset] + 1, indices[offset + 1] + 1, indices[offset + 2] + 1
        lines.append(f"f {a} {b} {c}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def glb_from_obj(text: str) -> bytes:
    vertices: list[tuple[float, float, float]] = []
    indices: list[int] = []
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 4:
            continue
        if parts[0] == "v":
            try:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif parts[0] == "f":
            face: list[int] = []
            for item in parts[1:]:
                token = item.split("/")[0]
                if not token:
                    continue
                try:
                    face.append(int(token) - 1)
                except ValueError:
                    face = []
                    break
            if len(face) < 3:
                continue
            for offset in range(1, len(face) - 1):
                indices.extend((face[0], face[offset], face[offset + 1]))
    if len(vertices) < 3 or len(indices) < 3:
        raise MeshError("The backend wrote an OBJ with no faces.")
    return pack_glb(vertices, indices)


def command_available(settings: dict | None = None) -> bool:
    return local_command(settings or {}) is not None


def triposr_root() -> Path | None:
    candidates = []
    override = os.environ.get("KOMA_TRIPOSR_DIR", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(data_dir() / "backends" / "triposr")
    for path in candidates:
        if (path / "run.py").is_file() or (path / "tsr").is_dir():
            return path
    return None


def local_command(settings: dict) -> list[str] | None:
    raw = str(settings.get("mesh_command") or "").strip()
    if not raw:
        raw = os.environ.get("KOMA_MESH_CMD", "").strip()
    if raw:
        parts = shlex.split(raw)
        return parts or None
    found = shutil.which("triposr")
    if found:
        return [found]
    if triposr_root() is not None:
        wrapper = ROOT / "extras" / "mesh" / "triposr_glb.py"
        if wrapper.is_file():
            return [sys.executable, str(wrapper)]
    return None


def generate_mesh(
    provider: str,
    prompt: str,
    images: list[str],
    status,
    settings: dict,
    transport=None,
    pause: float = 1.5,
    runner=None,
) -> bytes:
    """Return a GLB. `transport` and `runner` are tests' stand-ins for the network and the process."""
    chosen = provider or "local"
    if chosen == "local":
        image = images[0] if images else None
        return local_generate(image, settings, status, runner=runner)
    if chosen == "meshy":
        return meshy_generate(
            prompt,
            images,
            str(settings.get("meshy_key") or ""),
            status,
            transport or urlopen_transport,
            pause,
        )
    if chosen == "tripo":
        return tripo_generate(
            prompt,
            images,
            str(settings.get("tripo_key") or ""),
            status,
            transport or urlopen_transport,
            pause,
        )
    raise MeshError("Unknown 3D provider.")


def local_generate(image: str | None, settings: dict, status, runner=None) -> bytes:
    command = local_command(settings)
    if not command:
        raise MeshError(NOT_INSTALLED)
    if not image or not Path(image).is_file():
        raise MeshError("The local backend needs a picture. Draw or import a portrait first.")
    status("Lifting the picture into a mesh…")
    folder = Path(tempfile.mkdtemp(prefix="koma-mesh-"))
    target = folder / "model.glb"
    full = [*command, str(image), "--output", str(target)]
    try:
        if runner is not None:
            runner(full, target)
        else:
            try:
                completed = subprocess.run(full, capture_output=True, timeout=1800, check=False)
            except subprocess.TimeoutExpired as error:
                raise MeshError("The local 3D backend took too long and was stopped.") from error
            except OSError as error:
                raise MeshError(f"Could not start the local 3D backend ({error.strerror or error}).") from error
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace").strip()
                tail = detail[-400:] if detail else "It exited without a message."
                raise MeshError(f"The local 3D backend failed. {tail}")
        data = _read_output(target)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return data


def meshy_generate(prompt: str, images: list[str], key: str, status, transport, pause: float) -> bytes:
    if not key.strip():
        raise MeshError("Add a Meshy API key in Settings.")
    headers = {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"}
    if images:
        status("Sending the reference to Meshy…")
        body = json.dumps({"image_url": _data_uri(images[0]), "should_texture": True}).encode("utf-8")
        task = _meshy_id(_call(transport, "POST", f"{MESHY_API}/openapi/v1/image-to-3d", headers, body))
        result = _poll(
            f"{MESHY_API}/openapi/v1/image-to-3d/{task}",
            headers,
            transport,
            status,
            pause,
            "Meshy",
        )
    else:
        status("Sending the prompt to Meshy…")
        preview_body = json.dumps({"mode": "preview", "prompt": prompt}).encode("utf-8")
        preview = _meshy_id(_call(transport, "POST", f"{MESHY_API}/openapi/v2/text-to-3d", headers, preview_body))
        _poll(
            f"{MESHY_API}/openapi/v2/text-to-3d/{preview}",
            headers,
            transport,
            status,
            pause,
            "Meshy",
        )
        status("Refining the Meshy model…")
        refine_body = json.dumps({"mode": "refine", "preview_task_id": preview}).encode("utf-8")
        task = _meshy_id(_call(transport, "POST", f"{MESHY_API}/openapi/v2/text-to-3d", headers, refine_body))
        result = _poll(
            f"{MESHY_API}/openapi/v2/text-to-3d/{task}",
            headers,
            transport,
            status,
            pause,
            "Meshy",
        )
    url = str((result.get("model_urls") or {}).get("glb") or "")
    _require_https(url, "Meshy")
    status("Downloading the model…")
    _status, data = _call(transport, "GET", url, {}, None)
    if not data.startswith(b"glTF"):
        raise MeshError("Meshy did not return a GLB file.")
    return data


def tripo_generate(prompt: str, images: list[str], key: str, status, transport, pause: float) -> bytes:
    if not key.strip():
        raise MeshError("Add a Tripo API key in Settings.")
    headers = {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"}
    if images:
        status("Sending the reference to Tripo…")
        token = _tripo_upload(images[0], headers, transport)
        kind, mime = _image_kind(Path(images[0]).read_bytes())
        payload = {"type": "image_to_model", "file": {"type": kind, "file_token": token}}
        if mime:
            payload["file"]["type"] = kind
    else:
        status("Sending the prompt to Tripo…")
        payload = {"type": "text_to_model", "prompt": prompt}
    created = _tripo_json(_call(transport, "POST", f"{TRIPO_API}/v2/openapi/task", headers, json.dumps(payload).encode()))
    task_id = str((created.get("data") or {}).get("task_id") or "")
    if not task_id:
        raise MeshError("Tripo did not start a task.")
    result = _poll_tripo(f"{TRIPO_API}/v2/openapi/task/{task_id}", headers, transport, status, pause)
    output = result.get("output") or {}
    url = str(output.get("model") or output.get("pbr_model") or "")
    _require_https(url, "Tripo")
    status("Downloading the model…")
    _status, data = _call(transport, "GET", url, {}, None)
    if not data.startswith(b"glTF"):
        raise MeshError("Tripo did not return a GLB file.")
    return data


def urlopen_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float = 90):
    _require_https(url, "The 3D service")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        payload = error.read()
        text = _redact(payload.decode("utf-8", "replace"), headers)
        raise MeshError(f"The 3D service refused the request ({error.code}). {text[:300]}") from None
    except urllib.error.URLError as error:
        raise MeshError(f"Could not reach the 3D service ({error.reason}).") from None


def _call(transport, method: str, url: str, headers: dict, body: bytes | None):
    try:
        status, data = transport(method, url, headers, body)
    except MeshError:
        raise
    except Exception as error:
        raise MeshError(_redact(str(error), headers)[:300] or "The 3D service could not be reached.") from None
    if status >= 400:
        text = _redact(data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data), headers)
        raise MeshError(f"The 3D service refused the request ({status}). {text[:300]}")
    return status, data


def _json_body(data: bytes, headers: dict) -> dict:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise MeshError("The 3D service sent a reply Koma could not read.") from None
    if not isinstance(payload, dict):
        raise MeshError("The 3D service sent a reply Koma could not read.")
    return payload


def _meshy_id(result) -> str:
    _status, data = result
    payload = _json_body(data, {})
    task = payload.get("result") or payload.get("id") or ""
    if not isinstance(task, str) or not task.strip():
        raise MeshError("Meshy did not start a task.")
    return task.strip()


def _poll(url, headers, transport, status, pause, label) -> dict:
    for _ in range(_POLLS):
        _code, data = _call(transport, "GET", url, headers, None)
        payload = _json_body(data, headers)
        state = str(payload.get("status") or "")
        if state == "SUCCEEDED":
            return payload
        if state in {"FAILED", "CANCELED"}:
            raise MeshError(f"{label} could not build the model.")
        status(f"{label} is working…")
        if pause:
            time.sleep(pause)
    raise MeshError(f"{label} took too long.")


def _tripo_upload(path: str, headers: dict, transport) -> str:
    raw = Path(path).read_bytes()
    kind, mime = _image_kind(raw)
    boundary = "komaMeshBoundary7f3a"
    filename = f"reference.{kind if kind != 'jpeg' else 'jpg'}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")
    upload_headers = {
        "Authorization": headers["Authorization"],
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    payload = _tripo_json(_call(transport, "POST", f"{TRIPO_API}/v2/openapi/upload", upload_headers, body))
    token = str((payload.get("data") or {}).get("image_token") or "")
    if not token:
        raise MeshError("Tripo did not accept the reference image.")
    return token


def _tripo_json(result) -> dict:
    _status, data = result
    payload = _json_body(data, {})
    code = payload.get("code")
    if code not in (None, 0):
        raise MeshError("Tripo refused the request.")
    return payload


def _poll_tripo(url, headers, transport, status, pause) -> dict:
    for _ in range(_POLLS):
        payload = _tripo_json(_call(transport, "GET", url, headers, None))
        data = payload.get("data") or {}
        state = str(data.get("status") or "")
        if state == "success":
            return data
        if state in {"failed", "cancelled", "banned", "expired"}:
            raise MeshError("Tripo could not build the model.")
        status("Tripo is working…")
        if pause:
            time.sleep(pause)
    raise MeshError("Tripo took too long.")


def _read_output(target: Path) -> bytes:
    if target.is_file():
        data = target.read_bytes()
        if data.startswith(b"glTF"):
            return data
        return glb_from_obj(data.decode("utf-8", "replace"))
    obj = target.with_suffix(".obj")
    if obj.is_file():
        return glb_from_obj(obj.read_text(encoding="utf-8", errors="replace"))
    raise MeshError("The local 3D backend finished without a model file.")


def _data_uri(path: str) -> str:
    raw = Path(path).read_bytes()
    kind, mime = _image_kind(raw)
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _image_kind(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg", "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "png", "image/png"


def _require_https(url: str, label: str) -> None:
    if not str(url).startswith("https://"):
        raise MeshError(f"{label} returned a download link that is not HTTPS.")


def _redact(text: str, headers: dict) -> str:
    cleaned = text or ""
    auth = str(headers.get("Authorization") or "")
    token = auth.split(" ", 1)[-1].strip() if auth else ""
    if token and len(token) > 4:
        cleaned = cleaned.replace(token, "…")
    return cleaned


def _assemble(document: dict, blob: bytes) -> bytes:
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((4 - (len(raw) % 4)) % 4)
    total = 12 + 8 + len(raw) + 8 + len(blob)
    header = struct.pack("<4sII", b"glTF", 2, total)
    json_chunk = struct.pack("<I", len(raw)) + b"JSON" + raw
    bin_chunk = struct.pack("<I", len(blob)) + b"BIN\x00" + blob
    return header + json_chunk + bin_chunk


def _chunks(data: bytes) -> tuple[dict, bytes]:
    if len(data) < 20 or data[:4] != b"glTF":
        raise MeshError("That file is not a GLB.")
    offset = 12
    document = None
    blob = b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset : offset + length]
        offset += length
        if kind == 0x4E4F534A:
            document = json.loads(chunk.decode("utf-8").strip() or "{}")
        elif kind == 0x004E4942:
            blob = chunk
    if not isinstance(document, dict):
        raise MeshError("That GLB has no scene.")
    return document, blob


def _view(document: dict, blob: bytes, accessor_index: int) -> tuple[dict, dict, bytes]:
    accessors = document.get("accessors") or []
    views = document.get("bufferViews") or []
    if not isinstance(accessor_index, int) or accessor_index < 0 or accessor_index >= len(accessors):
        raise MeshError("That model is missing mesh data.")
    accessor = accessors[accessor_index]
    view_index = accessor.get("bufferView")
    if not isinstance(view_index, int) or view_index < 0 or view_index >= len(views):
        raise MeshError("That model is missing mesh data.")
    view = views[view_index]
    return accessor, view, blob


def _read_vec3(document: dict, blob: bytes, accessor_index: int) -> list[tuple[float, float, float]]:
    accessor, view, blob = _view(document, blob, accessor_index)
    if accessor.get("componentType") != 5126 or accessor.get("type") != "VEC3":
        raise MeshError("That model uses a position format Koma cannot read.")
    count = int(accessor.get("count") or 0)
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    stride = int(view.get("byteStride") or 12)
    values = []
    for index in range(count):
        offset = start + index * stride
        if offset + 12 > len(blob):
            break
        values.append(struct.unpack_from("<3f", blob, offset))
    return values


def _read_scalar(document: dict, blob: bytes, accessor_index: int) -> list[int]:
    accessor, view, blob = _view(document, blob, accessor_index)
    if accessor.get("type") != "SCALAR":
        raise MeshError("That model uses an index format Koma cannot read.")
    component = accessor.get("componentType")
    width = {5121: 1, 5123: 2, 5125: 4}.get(component)
    if width is None:
        raise MeshError("That model uses an index format Koma cannot read.")
    count = int(accessor.get("count") or 0)
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    stride = int(view.get("byteStride") or width)
    fmt = {1: "<B", 2: "<H", 4: "<I"}[width]
    values = []
    for index in range(count):
        offset = start + index * stride
        if offset + width > len(blob):
            break
        values.append(struct.unpack_from(fmt, blob, offset)[0])
    return values
