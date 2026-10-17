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
import urllib.parse
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
    cache: dict[int, list[tuple[float, float, float]]] = {}
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
            accessor_index = attributes["POSITION"]
            if accessor_index not in cache:
                cache[accessor_index] = _read_vec3(document, blob, accessor_index)
            chunk = cache[accessor_index]
            if "indices" in primitive:
                local = _read_scalar(document, blob, primitive["indices"])
            else:
                local = list(range(len(chunk)))
            # Primitives often share one big vertex buffer and only index a slice.
            # Keep the used vertices so a toon model does not balloon into a million points.
            remap: dict[int, int] = {}
            for index in local:
                if index < 0 or index >= len(chunk):
                    continue
                mapped = remap.get(index)
                if mapped is None:
                    mapped = len(positions)
                    remap[index] = mapped
                    positions.append(chunk[index])
                indices.append(mapped)
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


def glb_from_obj(text: str, root: Path | None = None) -> bytes:
    vertices: list[tuple[float, float, float]] = []
    texcoords: list[tuple[float, float]] = []
    materials = _parse_mtllib(text, root) if root is not None else {}
    groups: dict[str, list[tuple[int, int | None]]] = {}
    current = ""
    indexed = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "v" and len(parts) >= 4:
            try:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif parts[0] == "vt" and len(parts) >= 3:
            try:
                texcoords.append((float(parts[1]), float(parts[2])))
            except ValueError:
                continue
        elif parts[0] == "usemtl":
            current = line.split(None, 1)[1].strip() if len(parts) > 1 else ""
        elif parts[0] == "f" and len(parts) >= 4:
            corners: list[tuple[int, int | None]] = []
            for item in parts[1:]:
                bits = item.split("/")
                try:
                    corners.append((_obj_index(bits[0], len(vertices)), _obj_index(bits[1], len(texcoords)) if len(bits) > 1 and bits[1] else None))
                except ValueError:
                    corners = []
                    break
            if len(corners) < 3:
                continue
            if any(corner[1] is not None for corner in corners):
                indexed = True
            bucket = groups.setdefault(current, [])
            for offset in range(1, len(corners) - 1):
                bucket.extend((corners[0], corners[offset], corners[offset + 1]))
    flat = [corner for corners in groups.values() for corner in corners]
    if len(vertices) < 3 or len(flat) < 3:
        raise MeshError("The backend wrote an OBJ with no faces.")
    textured = indexed or any((materials.get(name) or {}).get("map") for name in groups)
    if not textured:
        indices = []
        for corners in groups.values():
            indices.extend(corner[0] for corner in corners)
        return pack_glb(vertices, indices)
    return _pack_obj_groups(vertices, texcoords, groups, materials, root)


def load_model_file(path: Path) -> bytes:
    """Read a GLB, VRM, or OBJ from disk. VRM is glTF, so it is stored as a GLB.

    Textures that sit next to the file are copied into that GLB. Parts that
    the file places with node transforms are moved into the mesh, so the
    viewer can draw the figure in its bind pose.
    """
    path = Path(path)
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in {".glb", ".vrm"} or raw.startswith(b"glTF"):
        if not raw.startswith(b"glTF"):
            raise MeshError("That file is not a GLB Koma can read.")
        return _prepare_glb(raw, path.parent)
    if suffix == ".obj":
        return glb_from_obj(raw.decode("utf-8", "replace"), path.parent)
    raise MeshError("Import a .glb, .vrm, or .obj file.")


def _obj_index(token: str, count: int) -> int:
    index = int(token)
    if index < 0:
        index = count + index
    else:
        index -= 1
    if index < 0 or index >= count:
        raise ValueError(token)
    return index


def _parse_mtllib(text: str, root: Path) -> dict[str, dict]:
    materials: dict[str, dict] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.lower().startswith("mtllib "):
            continue
        name = line.split(None, 1)[1].strip()
        try:
            mtl_path = _texture_path(name, root)
        except MeshError:
            continue
        materials.update(_parse_mtl(mtl_path.read_text(encoding="utf-8", errors="replace")))
    return materials


def _parse_mtl(text: str) -> dict[str, dict]:
    materials: dict[str, dict] = {}
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "newmtl":
            current = {"color": (1.0, 1.0, 1.0), "map": ""}
            materials[line.split(None, 1)[1].strip() if len(parts) > 1 else ""] = current
        elif current is None:
            continue
        elif parts[0] == "Kd" and len(parts) >= 4:
            try:
                current["color"] = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError:
                continue
        elif parts[0] == "map_Kd":
            tokens = parts[1:]
            kept: list[str] = []
            skip = False
            for token in tokens:
                if skip:
                    skip = False
                    continue
                if token.startswith("-"):
                    skip = True
                    continue
                kept.append(token)
            current["map"] = " ".join(kept).strip()
    return materials


def _pack_obj_groups(
    vertices: list[tuple[float, float, float]],
    texcoords: list[tuple[float, float]],
    groups: dict[str, list[tuple[int, int | None]]],
    materials: dict[str, dict],
    root: Path | None,
) -> bytes:
    images: list[bytes] = []
    image_of: dict[str, int] = {}
    primitives = []
    blob = bytearray()
    accessors = []
    views = []
    material_list = []
    for name, corners in groups.items():
        if len(corners) < 3:
            continue
        info = materials.get(name) or {}
        image_index = None
        map_name = str(info.get("map") or "")
        if map_name and root is not None:
            if map_name not in image_of:
                image_of[map_name] = len(images)
                images.append(_read_texture_uri(map_name, root))
            image_index = image_of[map_name]
        positions = bytearray()
        uvs = bytearray()
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        for vertex_index, uv_index in corners:
            x, y, z = vertices[vertex_index]
            positions.extend(struct.pack("<3f", x, y, z))
            xs.append(x)
            ys.append(y)
            zs.append(z)
            if uv_index is None:
                u, v = 0.0, 0.0
            else:
                u, v = texcoords[uv_index]
                v = 1.0 - v
            uvs.extend(struct.pack("<2f", u, v))
        count = len(corners)
        index_bytes, component = _index_bytes(list(range(count)))
        position_view = _append_view(blob, views, positions, 34962)
        uv_view = _append_view(blob, views, uvs, 34962)
        index_view = _append_view(blob, views, index_bytes, 34963)
        position_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": position_view,
                "componentType": 5126,
                "count": count,
                "type": "VEC3",
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            }
        )
        uv_accessor = len(accessors)
        accessors.append({"bufferView": uv_view, "componentType": 5126, "count": count, "type": "VEC2"})
        index_accessor = len(accessors)
        accessors.append({"bufferView": index_view, "componentType": component, "count": count, "type": "SCALAR"})
        color = info.get("color") or (1.0, 1.0, 1.0)
        material_index = len(material_list)
        material: dict = {
            "name": name or "material",
            "pbrMetallicRoughness": {"baseColorFactor": [color[0], color[1], color[2], 1.0], "metallicFactor": 0.0},
        }
        if image_index is not None:
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": image_index}
        material_list.append(material)
        primitives.append(
            {
                "attributes": {"POSITION": position_accessor, "TEXCOORD_0": uv_accessor},
                "indices": index_accessor,
                "material": material_index,
                "mode": 4,
            }
        )
    if not primitives:
        raise MeshError("The backend wrote an OBJ with no faces.")
    _append_images(blob, views, images)
    document = {
        "asset": {"version": "2.0", "generator": "koma"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": primitives}],
        "materials": material_list,
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    if images:
        image_views = list(range(len(views) - len(images), len(views)))
        document["images"] = [
            {"bufferView": view_index, "mimeType": _image_kind(raw)[1]}
            for raw, view_index in zip(images, image_views)
        ]
        document["textures"] = [{"source": index} for index in range(len(images))]
        document["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}]
    declared = len(blob)
    blob.extend(b"\x00" * ((4 - (len(blob) % 4)) % 4))
    document["buffers"][0]["byteLength"] = declared
    return _assemble(document, bytes(blob))


def _index_bytes(indices: list[int]) -> tuple[bytes, int]:
    if any(index > 65535 for index in indices):
        return b"".join(struct.pack("<I", index) for index in indices), 5125
    return b"".join(struct.pack("<H", index) for index in indices), 5123


def _append_view(blob: bytearray, views: list[dict], data: bytes, target: int) -> int:
    pad = (4 - (len(blob) % 4)) % 4
    blob.extend(b"\x00" * pad)
    offset = len(blob)
    blob.extend(data)
    views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
    return len(views) - 1


def _append_images(blob: bytearray, views: list[dict], images: list[bytes]) -> None:
    for raw in images:
        pad = (4 - (len(blob) % 4)) % 4
        blob.extend(b"\x00" * pad)
        offset = len(blob)
        blob.extend(raw)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(raw)})


def _prepare_glb(data: bytes, root: Path) -> bytes:
    data = _embed_external_images(data, root)
    return _bake_scene_nodes(data)


def _embed_external_images(data: bytes, root: Path) -> bytes:
    document, blob = _chunks(data)
    images = document.get("images") or []
    if not any(isinstance(image, dict) and image.get("uri") for image in images):
        return data
    buffers = document.setdefault("buffers", [{"byteLength": len(blob)}])
    declared = int((buffers[0] or {}).get("byteLength") or len(blob))
    raw_blob = bytearray(blob[:declared])
    views = document.setdefault("bufferViews", [])
    for image in images:
        if not isinstance(image, dict) or not image.get("uri"):
            continue
        texture = _read_texture_uri(str(image["uri"]), root)
        pad = (4 - (len(raw_blob) % 4)) % 4
        raw_blob.extend(b"\x00" * pad)
        offset = len(raw_blob)
        raw_blob.extend(texture)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(texture)})
        image.pop("uri", None)
        image["bufferView"] = len(views) - 1
        image["mimeType"] = _image_kind(texture)[1]
    buffers[0]["byteLength"] = len(raw_blob)
    return _assemble(document, bytes(raw_blob))


def _read_texture_uri(uri: str, root: Path) -> bytes:
    text = uri.strip()
    lower = text.lower()
    if lower.startswith("data:"):
        header, _, payload = text.partition(",")
        if ";base64" in header.lower():
            try:
                return base64.b64decode(payload, validate=False)
            except ValueError as error:
                raise MeshError("That model has a texture Koma could not read.") from error
        return urllib.parse.unquote_to_bytes(payload)
    if lower.startswith(("http://", "https://", "file:", "//")):
        raise MeshError("That model points at a texture on the web. Koma only reads textures beside the file.")
    return _texture_path(text, root).read_bytes()


def _texture_path(uri: str, root: Path) -> Path:
    relative = urllib.parse.unquote(uri.split("?", 1)[0].split("#", 1)[0]).replace("\\", "/")
    if not relative or relative.startswith("/") or Path(relative).is_absolute():
        raise MeshError("That model points at a texture outside its folder.")
    base = root.resolve()
    candidate = (base / relative).resolve()
    if candidate != base and base not in candidate.parents:
        raise MeshError("That model points at a texture outside its folder.")
    if not candidate.is_file():
        raise MeshError(f"That model is missing its texture ({Path(relative).name}).")
    return candidate


def _bake_scene_nodes(data: bytes) -> bytes:
    document, blob = _chunks(data)
    nodes = document.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        return data
    worlds = _world_matrices(nodes, document.get("scenes") or [])
    users: dict[int, list[int]] = {}
    for index, node in enumerate(nodes):
        if isinstance(node, dict) and isinstance(node.get("mesh"), int):
            users.setdefault(node["mesh"], []).append(index)
    mutable = bytearray(blob)
    baked: set[int] = set()
    changed = False
    for mesh_index, node_indices in users.items():
        meshes = document.get("meshes") or []
        if mesh_index < 0 or mesh_index >= len(meshes):
            continue
        matrices = [worlds[index] for index in node_indices]
        if all(_is_identity(matrix) for matrix in matrices):
            continue
        if len({tuple(round(value, 5) for value in matrix) for matrix in matrices}) > 1:
            raise MeshError("That model repeats one mesh in different places. Koma cannot place the copies yet.")
        matrix = matrices[0]
        for primitive in meshes[mesh_index].get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes") or {}
            if "POSITION" not in attributes:
                continue
            if not _transform_positions(document, mutable, attributes["POSITION"], matrix, True, baked):
                raise MeshError("That model uses a position format Koma cannot place.")
            for target in primitive.get("targets") or []:
                if isinstance(target, dict) and "POSITION" in target:
                    _transform_positions(document, mutable, target["POSITION"], matrix, False, baked)
        changed = True
    if not changed:
        return data
    for node in nodes:
        if isinstance(node, dict):
            node.pop("matrix", None)
            node.pop("translation", None)
            node.pop("rotation", None)
            node.pop("scale", None)
    return _assemble(document, bytes(mutable))


def _world_matrices(nodes: list, scenes: list) -> list[list[float]]:
    parent_of: dict[int, int] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for child in node.get("children") or []:
            if isinstance(child, int):
                parent_of[child] = index
    cache: dict[int, list[float]] = {}

    def walk(index: int) -> list[float]:
        if index in cache:
            return cache[index]
        local = _node_matrix(nodes[index] if isinstance(nodes[index], dict) else {})
        parent = parent_of.get(index)
        cache[index] = _mul_matrix(walk(parent), local) if parent is not None else local
        return cache[index]

    roots: list[int] = []
    for scene in scenes:
        if isinstance(scene, dict):
            roots.extend(index for index in scene.get("nodes") or [] if isinstance(index, int))
    if not roots:
        roots = list(range(len(nodes)))
    for index in range(len(nodes)):
        walk(index)
    return [cache[index] for index in range(len(nodes))]


def _node_matrix(node: dict) -> list[float]:
    if isinstance(node.get("matrix"), list) and len(node["matrix"]) == 16:
        return [float(value) for value in node["matrix"]]
    translation = node.get("translation") or [0.0, 0.0, 0.0]
    rotation = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    scale = node.get("scale") or [1.0, 1.0, 1.0]
    tx, ty, tz = (float(translation[0]), float(translation[1]), float(translation[2]))
    x, y, z, w = (float(rotation[0]), float(rotation[1]), float(rotation[2]), float(rotation[3]))
    sx, sy, sz = (float(scale[0]), float(scale[1]), float(scale[2]))
    return [
        (1 - 2 * y * y - 2 * z * z) * sx,
        (2 * x * y + 2 * z * w) * sx,
        (2 * x * z - 2 * y * w) * sx,
        0.0,
        (2 * x * y - 2 * z * w) * sy,
        (1 - 2 * x * x - 2 * z * z) * sy,
        (2 * y * z + 2 * x * w) * sy,
        0.0,
        (2 * x * z + 2 * y * w) * sz,
        (2 * y * z - 2 * x * w) * sz,
        (1 - 2 * x * x - 2 * y * y) * sz,
        0.0,
        tx,
        ty,
        tz,
        1.0,
    ]


def _mul_matrix(parent: list[float], local: list[float]) -> list[float]:
    out = [0.0] * 16
    for column in range(4):
        for row in range(4):
            out[column * 4 + row] = (
                parent[row] * local[column * 4]
                + parent[4 + row] * local[column * 4 + 1]
                + parent[8 + row] * local[column * 4 + 2]
                + parent[12 + row] * local[column * 4 + 3]
            )
    return out


def _is_identity(matrix: list[float]) -> bool:
    identity = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    return all(abs(matrix[index] - identity[index]) < 1e-5 for index in range(16))


def _transform_positions(document: dict, blob: bytearray, accessor_index: int, matrix: list[float], translate: bool, baked: set[int]) -> bool:
    if not isinstance(accessor_index, int) or accessor_index in baked:
        return True
    accessor, view, _blob = _view(document, bytes(blob), accessor_index)
    if accessor.get("componentType") != 5126 or accessor.get("type") != "VEC3":
        return False
    count = int(accessor.get("count") or 0)
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    stride = int(view.get("byteStride") or 12)
    if start < 0 or stride < 12:
        return False
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for index in range(count):
        offset = start + index * stride
        if offset + 12 > len(blob):
            return False
        x, y, z = struct.unpack_from("<3f", blob, offset)
        if translate:
            nx = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]
            ny = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]
            nz = matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
        else:
            nx = matrix[0] * x + matrix[4] * y + matrix[8] * z
            ny = matrix[1] * x + matrix[5] * y + matrix[9] * z
            nz = matrix[2] * x + matrix[6] * y + matrix[10] * z
        struct.pack_into("<3f", blob, offset, nx, ny, nz)
        xs.append(nx)
        ys.append(ny)
        zs.append(nz)
    if xs:
        accessor["min"] = [min(xs), min(ys), min(zs)]
        accessor["max"] = [max(xs), max(ys), max(zs)]
    baked.add(accessor_index)
    return True


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
