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
import math
import os
import re
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


def bake_posed_glb(
    positions: list[tuple[float, float, float]],
    indices: list[int],
    uvs: list[tuple[float, float]] | None = None,
    ranges: list[dict] | None = None,
    images: list[bytes] | None = None,
) -> bytes:
    """Write the posed mesh as a new GLB. The file it came from is not changed."""
    if len(positions) < 3 or len(indices) < 3 or len(indices) % 3:
        raise MeshError("That posed model has no faces.")
    count = len(positions)
    for index in indices:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= count:
            raise MeshError("That posed model has a face Koma cannot write.")
    if not uvs or len(uvs) != count:
        uvs = [(0.0, 0.0)] * count
    if not ranges:
        ranges = [{"start": 0, "count": len(indices), "color": (0.78, 0.78, 0.8), "image": None}]
    images = [raw for raw in (images or []) if isinstance(raw, (bytes, bytearray)) and raw]
    normals = _vertex_normals(positions, indices)
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    xs = [point[0] for point in positions]
    ys = [point[1] for point in positions]
    zs = [point[2] for point in positions]
    position_view = _append_view(blob, views, b"".join(struct.pack("<3f", *point) for point in positions), 34962)
    normal_view = _append_view(blob, views, b"".join(struct.pack("<3f", *normal) for normal in normals), 34962)
    uv_view = _append_view(blob, views, b"".join(struct.pack("<2f", u, v) for u, v in uvs), 34962)
    accessors.extend(
        [
            {
                "bufferView": position_view,
                "componentType": 5126,
                "count": count,
                "type": "VEC3",
                "min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
            },
            {"bufferView": normal_view, "componentType": 5126, "count": count, "type": "VEC3"},
            {"bufferView": uv_view, "componentType": 5126, "count": count, "type": "VEC2"},
        ]
    )
    primitives = []
    materials = []
    for raw in ranges:
        if not isinstance(raw, dict):
            continue
        start = int(raw.get("start") or 0)
        length = int(raw.get("count") or 0)
        if length < 3 or length % 3 or start < 0 or start + length > len(indices):
            continue
        color = raw.get("color") or (0.78, 0.78, 0.8)
        try:
            rgb = [min(1.0, max(0.0, float(color[0]))), min(1.0, max(0.0, float(color[1]))), min(1.0, max(0.0, float(color[2])))]
        except (TypeError, ValueError, IndexError):
            rgb = [0.78, 0.78, 0.8]
        material: dict = {
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorFactor": [rgb[0], rgb[1], rgb[2], 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
        }
        image_index = raw.get("image")
        if isinstance(image_index, int) and not isinstance(image_index, bool) and 0 <= image_index < len(images):
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": image_index}
        materials.append(material)
        index_bytes, component = _index_bytes(list(indices[start : start + length]))
        index_view = _append_view(blob, views, index_bytes, 34963)
        accessors.append({"bufferView": index_view, "componentType": component, "count": length, "type": "SCALAR"})
        primitives.append(
            {
                "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                "indices": len(accessors) - 1,
                "material": len(materials) - 1,
                "mode": 4,
            }
        )
    if not primitives:
        raise MeshError("That posed model has no faces.")
    _append_images(blob, views, images)
    document: dict = {
        "asset": {"version": "2.0", "generator": "koma"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "Posed"}],
        "meshes": [{"name": "Posed", "primitives": primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    if images:
        image_views = list(range(len(views) - len(images), len(views)))
        document["images"] = [
            {"bufferView": view_index, "mimeType": _image_kind(bytes(raw))[1]}
            for raw, view_index in zip(images, image_views)
        ]
        document["textures"] = [{"sampler": 0, "source": index} for index in range(len(images))]
        document["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}]
    declared = len(blob)
    blob.extend(b"\x00" * ((4 - (declared % 4)) % 4))
    document["buffers"][0]["byteLength"] = declared
    return _assemble(document, bytes(blob))


def _vertex_normals(
    positions: list[tuple[float, float, float]],
    indices: list[int],
) -> list[tuple[float, float, float]]:
    totals = [[0.0, 0.0, 0.0] for _ in positions]
    for offset in range(0, len(indices) - 2, 3):
        a, b, c = indices[offset], indices[offset + 1], indices[offset + 2]
        ax, ay, az = positions[a]
        bx, by, bz = positions[b]
        cx, cy, cz = positions[c]
        nx = (by - ay) * (cz - az) - (bz - az) * (cy - ay)
        ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az)
        nz = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        for index in (a, b, c):
            totals[index][0] += nx
            totals[index][1] += ny
            totals[index][2] += nz
    normals = []
    for nx, ny, nz in totals:
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-8:
            normals.append((0.0, 1.0, 0.0))
        else:
            normals.append((nx / length, ny / length, nz / length))
    return normals


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


def blend_shape_names(data: bytes) -> list[str]:
    """Expression names stored on a GLB or VRM. Neutral is the rest pose."""
    document, _blob = _chunks(data)
    found: list[str] = []

    def add(name: object) -> None:
        cleaned = str(name or "").strip()
        if not cleaned or cleaned.lower() == "neutral" or cleaned in found:
            return
        found.append(cleaned)

    extensions = document.get("extensions") or {}
    master = ((extensions.get("VRM") or {}).get("blendShapeMaster") or {})
    for group in master.get("blendShapeGroups") or []:
        if not isinstance(group, dict) or group.get("presetName") == "neutral":
            continue
        add(group.get("name") or group.get("presetName"))
    expressions = ((extensions.get("VRMC_vrm") or {}).get("expressions") or {})
    for bucket in ("preset", "custom"):
        table = expressions.get(bucket) or {}
        if not isinstance(table, dict):
            continue
        for key, expr in table.items():
            if key == "neutral":
                continue
            add((expr or {}).get("name") if isinstance(expr, dict) else key)
    if found:
        return found[:12]
    for mesh in document.get("meshes") or []:
        extras = (mesh or {}).get("extras") or {}
        for name in extras.get("targetNames") or []:
            add(name)
    return found[:12]


def read_morph(data: bytes, mesh_index: int = 0, target_index: int = 0) -> list[tuple[float, float, float]]:
    """Position deltas for one morph target, in the file's vertex order."""
    document, blob = _chunks(data)
    meshes = document.get("meshes") or []
    if mesh_index < 0 or mesh_index >= len(meshes):
        raise MeshError("That model has no expression there.")
    primitives = meshes[mesh_index].get("primitives") or []
    if not primitives:
        raise MeshError("That model has no expression there.")
    targets = primitives[0].get("targets") or []
    if target_index < 0 or target_index >= len(targets):
        raise MeshError("That model has no expression there.")
    accessor = (targets[target_index] or {}).get("POSITION")
    if accessor is None:
        raise MeshError("That expression does not move the mesh.")
    return _read_vec3(document, blob, accessor)


def apply_morph(
    positions: list[tuple[float, float, float]],
    deltas: list[tuple[float, float, float]],
    weight: float = 1.0,
) -> list[tuple[float, float, float]]:
    """Rest pose plus one morph. The viewer uses the same addition."""
    moved = []
    for index, point in enumerate(positions):
        delta = deltas[index] if index < len(deltas) else (0.0, 0.0, 0.0)
        moved.append((point[0] + delta[0] * weight, point[1] + delta[1] * weight, point[2] + delta[2] * weight))
    return moved


def humanoid_bones(data: bytes) -> dict[str, int]:
    """VRM humanoid names to node indexes, or a guess from node names."""
    document, _blob = _chunks(data)
    mapped = _vrm_humanoid(document)
    if mapped:
        return mapped
    return humanoid_from_names(_named_nodes(document))


def humanoid_from_names(nodes: list[tuple[int, str, int | None]]) -> dict[str, int]:
    """Map (index, name, parent) rows onto humanoid bone names."""
    assigned, used = _direct_bones(nodes)
    spine = _name_chain(nodes, ("hips", "pelvis", "spine", "chest", "torso", "abdomen"), "", used)
    _take_chain(assigned, used, spine, ("hips", "spine", "chest", "upperChest"))
    neck = _name_chain(nodes, ("neck", "head"), "", used)
    if neck and "neck" not in assigned:
        assigned["neck"] = neck[0]
        used.add(neck[0])
        neck = neck[1:]
    if neck and "head" not in assigned:
        assigned["head"] = neck[-1]
        used.add(neck[-1])
    for side in ("left", "right"):
        arms = _name_chain(nodes, ("shoulder", "arm", "forearm", "elbow", "wrist", "hand"), side, used)
        _take_chain(assigned, used, arms, _fit_slots(len(arms), (
            f"{side}Shoulder", f"{side}UpperArm", f"{side}LowerArm", f"{side}Hand",
        ), True))
        legs = _name_chain(nodes, ("upleg", "thigh", "leg", "calf", "shin", "knee", "foot", "toe", "ball"), side, used)
        _take_chain(assigned, used, legs, _fit_slots(len(legs), (
            f"{side}UpperLeg", f"{side}LowerLeg", f"{side}Foot", f"{side}Toes",
        ), False))
    return assigned


def skin_points(
    points: list[tuple[float, float, float]],
    joints: list[tuple[int, int, int, int]],
    weights: list[tuple[float, float, float, float]],
    matrices: list[list[float]],
) -> list[tuple[float, float, float]]:
    """Linear blend skinning. `matrices` are joint matrices in the point's space."""
    skinned = []
    for index, point in enumerate(points):
        ids = joints[index] if index < len(joints) else (0, 0, 0, 0)
        blend = weights[index] if index < len(weights) else (1.0, 0.0, 0.0, 0.0)
        total = [0.0, 0.0, 0.0]
        covered = 0.0
        for slot in range(4):
            weight = float(blend[slot])
            if weight == 0.0:
                continue
            joint = ids[slot]
            if joint < 0 or joint >= len(matrices):
                continue
            moved = _mul_point(matrices[joint], point)
            total[0] += moved[0] * weight
            total[1] += moved[1] * weight
            total[2] += moved[2] * weight
            covered += weight
        if covered == 0.0:
            skinned.append(point)
        else:
            skinned.append((total[0], total[1], total[2]))
    return skinned


def joint_matrix(mesh_world: list[float], joint_world: list[float], inverse_bind: list[float]) -> list[float]:
    """glTF joint matrix that reproduces the bind pose when worlds are the bind worlds."""
    return _mul_matrix(_mul_matrix(_invert_matrix(mesh_world), joint_world), inverse_bind)


def _mul_point(matrix: list[float], point: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = point
    return (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
    )


def _invert_matrix(matrix: list[float]) -> list[float]:
    rows = [[matrix[column * 4 + row] for column in range(4)] for row in range(4)]
    inverse = [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        rows[column], rows[pivot] = rows[pivot], rows[column]
        inverse[column], inverse[pivot] = inverse[pivot], inverse[column]
        scale = rows[column][column]
        for cell in range(4):
            rows[column][cell] /= scale
            inverse[column][cell] /= scale
        for row in range(4):
            if row == column:
                continue
            factor = rows[row][column]
            for cell in range(4):
                rows[row][cell] -= factor * rows[column][cell]
                inverse[row][cell] -= factor * inverse[column][cell]
    out = [0.0] * 16
    for row in range(4):
        for column in range(4):
            out[column * 4 + row] = inverse[row][column]
    return out


_BONE_ALIASES = (
    ("hips", ("hips", "pelvis")),
    ("spine", ("spine",)),
    ("chest", ("chest",)),
    ("upperChest", ("upperchest",)),
    ("neck", ("neck",)),
    ("head", ("head",)),
    ("leftShoulder", ("leftshoulder", "lshoulder", "shoulderl")),
    ("leftUpperArm", ("leftupperarm", "leftarm", "lupperarm", "upperarml")),
    ("leftLowerArm", ("leftlowerarm", "leftforearm", "lforearm", "forearml", "lowerarml")),
    ("leftHand", ("lefthand", "lhand", "handl")),
    ("rightShoulder", ("rightshoulder", "rshoulder", "shoulderr")),
    ("rightUpperArm", ("rightupperarm", "rightarm", "rupperarm", "upperarmr")),
    ("rightLowerArm", ("rightlowerarm", "rightforearm", "rforearm", "forearmr", "lowerarmr")),
    ("rightHand", ("righthand", "rhand", "handr")),
    ("leftUpperLeg", ("leftupperleg", "leftupleg", "leftthigh", "lupleg", "thighl")),
    ("leftLowerLeg", ("leftlowerleg", "leftleg", "leftcalf", "lcalf", "calfl")),
    ("leftFoot", ("leftfoot", "lfoot", "footl")),
    ("leftToes", ("lefttoes", "lefttoe", "ltoe", "toel")),
    ("rightUpperLeg", ("rightupperleg", "rightupleg", "rightthigh", "rupleg", "thighr")),
    ("rightLowerLeg", ("rightlowerleg", "rightleg", "rightcalf", "rcalf", "calfr")),
    ("rightFoot", ("rightfoot", "rfoot", "footr")),
    ("rightToes", ("righttoes", "righttoe", "rtoe", "toer")),
)


def _vrm_humanoid(document: dict) -> dict[str, int]:
    found: dict[str, int] = {}
    extensions = document.get("extensions") or {}
    human = ((extensions.get("VRM") or {}).get("humanoid") or {})
    for bone in human.get("humanBones") or []:
        if not isinstance(bone, dict):
            continue
        name = bone.get("bone")
        node = bone.get("node")
        if isinstance(name, str) and isinstance(node, int):
            found[name] = node
    bones = (((extensions.get("VRMC_vrm") or {}).get("humanoid") or {}).get("humanBones") or {})
    if isinstance(bones, dict):
        for name, spec in bones.items():
            node = spec.get("node") if isinstance(spec, dict) else None
            if isinstance(name, str) and isinstance(node, int):
                found[name] = node
    return found


def _named_nodes(document: dict) -> list[tuple[int, str, int | None]]:
    nodes = document.get("nodes") or []
    parent_of: dict[int, int] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for child in node.get("children") or []:
            if isinstance(child, int):
                parent_of[child] = index
    named = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        named.append((index, str(node.get("name") or ""), parent_of.get(index)))
    return named


def _compact_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _bone_side(name: str) -> str:
    raw = name.lower()
    if "left" in raw or re.search(r"(^|[_\.])l($|[_\.])", raw) or "joint_l" in raw:
        return "left"
    if "right" in raw or re.search(r"(^|[_\.])r($|[_\.])", raw) or "joint_r" in raw:
        return "right"
    return ""


def _direct_bones(nodes: list[tuple[int, str, int | None]]) -> tuple[dict[str, int], set[int]]:
    assigned: dict[str, int] = {}
    used: set[int] = set()
    compacted = [(index, _compact_name(name)) for index, name, _parent in nodes]
    for bone, aliases in _BONE_ALIASES:
        best = None
        best_score = None
        for index, compact in compacted:
            if index in used or not compact:
                continue
            for alias in aliases:
                if alias not in compact:
                    continue
                score = (-len(alias), 0 if compact == alias else 1, len(compact))
                if best_score is None or score < best_score:
                    best_score = score
                    best = index
        if best is not None:
            assigned[bone] = best
            used.add(best)
    return assigned, used


def _node_depth(index: int, parent_of: dict[int, int | None]) -> int:
    depth = 0
    seen = set()
    while index in parent_of and parent_of[index] is not None and index not in seen:
        seen.add(index)
        index = parent_of[index]
        depth += 1
    return depth


def _name_chain(nodes: list[tuple[int, str, int | None]], words: tuple[str, ...], side: str, used: set[int]) -> list[int]:
    parent_of = {index: parent for index, _name, parent in nodes}
    picked = []
    for index, name, _parent in nodes:
        if index in used:
            continue
        if side and _bone_side(name) != side:
            continue
        if side == "" and _bone_side(name):
            continue
        compact = _compact_name(name)
        if any(word in compact for word in words):
            picked.append(index)
    picked.sort(key=lambda index: (_node_depth(index, parent_of), index))
    return picked


def _fit_slots(count: int, slots: tuple[str, ...], drop_first: bool) -> tuple[str, ...]:
    if count <= 0:
        return ()
    if count >= len(slots):
        return slots
    if drop_first and len(slots) > 1:
        return slots[1 : 1 + count]
    return slots[:count]


def _take_chain(assigned: dict[str, int], used: set[int], chain: list[int], slots: tuple[str, ...]) -> None:
    cursor = 0
    for bone in slots:
        if not bone or bone in assigned:
            continue
        if cursor >= len(chain):
            break
        assigned[bone] = chain[cursor]
        used.add(chain[cursor])
        cursor += 1


def animation_clips(data: bytes) -> list[dict]:
    """Joint clips stored in a GLB or VRM. Weights and sparse tracks are skipped."""
    document, blob = _chunks(data)
    clips = []
    for anim in document.get("animations") or []:
        if not isinstance(anim, dict):
            continue
        channels = []
        duration = 0.0
        samplers = anim.get("samplers") or []
        for channel in anim.get("channels") or []:
            if not isinstance(channel, dict):
                continue
            target = channel.get("target") or {}
            path = target.get("path")
            node = target.get("node")
            sampler_index = channel.get("sampler")
            if path not in {"translation", "rotation", "scale"} or not isinstance(node, int):
                continue
            if not isinstance(sampler_index, int) or sampler_index < 0 or sampler_index >= len(samplers):
                continue
            sampler = samplers[sampler_index] or {}
            interpolation = sampler.get("interpolation") or "LINEAR"
            if interpolation not in {"LINEAR", "STEP", "CUBICSPLINE"}:
                interpolation = "LINEAR"
            times = [row[0] for row in _read_float_vectors(document, blob, sampler.get("input"))]
            values = _read_float_vectors(document, blob, sampler.get("output"))
            if len(times) < 1 or not values:
                continue
            if interpolation == "CUBICSPLINE":
                if len(values) != len(times) * 3:
                    continue
            elif len(values) != len(times):
                continue
            duration = max(duration, times[-1])
            channels.append(
                {
                    "node": node,
                    "path": path,
                    "interpolation": interpolation,
                    "times": times,
                    "values": values,
                }
            )
        if not channels:
            continue
        name = str(anim.get("name") or "").strip() or f"Clip {len(clips) + 1}"
        clips.append({"name": name, "duration": duration, "channels": channels})
    return clips


def sample_channel(times: list[float], values: list[list[float]], time: float, interpolation: str) -> list[float]:
    """One track at `time`. Rotation LINEAR uses slerp. Cubic uses the glTF spline."""
    if not times or not values:
        return []
    interpolation = interpolation or "LINEAR"
    if time <= times[0]:
        return _quat_norm(_key_value(values, 0, interpolation)) if _is_quat(values, interpolation) else list(_key_value(values, 0, interpolation))
    if time >= times[-1]:
        last = len(times) - 1
        picked = _key_value(values, last, interpolation)
        return _quat_norm(picked) if _is_quat(values, interpolation) else list(picked)
    hi = 1
    while hi < len(times) and times[hi] < time:
        hi += 1
    lo = hi - 1
    span = times[hi] - times[lo]
    local = 0.0 if span <= 1e-8 else (time - times[lo]) / span
    if interpolation == "STEP":
        picked = _key_value(values, lo, interpolation)
        return _quat_norm(picked) if _is_quat(values, interpolation) else list(picked)
    if interpolation == "CUBICSPLINE":
        picked = _cubic_point(values, lo, local, span)
        return _quat_norm(picked) if len(picked) == 4 else picked
    start = _key_value(values, lo, interpolation)
    end = _key_value(values, hi, interpolation)
    if len(start) == 4 and len(end) == 4:
        return _quat_slerp(start, end, local)
    return [start[index] + (end[index] - start[index]) * local for index in range(len(start))]


def sample_clip(clip: dict, time: float, loop: bool = True) -> dict[int, dict[str, list[float]]]:
    """Local translation, rotation, and scale for each animated node."""
    duration = float(clip.get("duration") or 0.0)
    at = _clip_time(duration, float(time), loop)
    posed: dict[int, dict[str, list[float]]] = {}
    for channel in clip.get("channels") or []:
        sampled = sample_channel(channel["times"], channel["values"], at, channel.get("interpolation") or "LINEAR")
        if not sampled:
            continue
        node = int(channel["node"])
        slot = posed.setdefault(node, {})
        path = channel["path"]
        if path == "translation":
            slot["translation"] = sampled[:3]
        elif path == "rotation":
            slot["rotation"] = _quat_norm(sampled[:4])
        elif path == "scale":
            slot["scale"] = sampled[:3]
    return posed


def retarget_clips(source: bytes, target: bytes) -> list[dict]:
    """Copy clips onto `target` by bone name, then by the humanoid map.

    Same names keep the authored tracks. A humanoid match turns each rotation
    into the same change from that bone's bind pose, and scales hip travel by
    the two bodies. The source file is not modified.
    """
    source_doc, _source_blob = _chunks(source)
    target_doc, _target_blob = _chunks(target)
    source_nodes = source_doc.get("nodes") or []
    target_nodes = target_doc.get("nodes") or []
    if not isinstance(source_nodes, list) or not isinstance(target_nodes, list):
        raise MeshError("That animation does not match this rig.")
    clips = animation_clips(source)
    if not clips:
        raise MeshError("That file has no animation.")
    target_by_name: dict[str, int] = {}
    for index, node in enumerate(target_nodes):
        if not isinstance(node, dict):
            continue
        key = _compact_name(str(node.get("name") or ""))
        if key and key not in target_by_name:
            target_by_name[key] = index
    mapping: dict[int, tuple[int, str]] = {}
    for index, node in enumerate(source_nodes):
        if not isinstance(node, dict):
            continue
        key = _compact_name(str(node.get("name") or ""))
        if key and key in target_by_name:
            mapping[index] = (target_by_name[key], "name")
    source_human = humanoid_bones(source)
    target_human = humanoid_bones(target)
    humanoid_hits = 0
    for bone, src in source_human.items():
        dst = target_human.get(bone)
        if not isinstance(src, int) or not isinstance(dst, int) or src in mapping:
            continue
        mapping[src] = (dst, "humanoid")
        humanoid_hits += 1
    name_hits = sum(1 for _src, (_dst, kind) in mapping.items() if kind == "name")
    if name_hits < 1 and humanoid_hits < 6:
        raise MeshError("That animation does not match this rig.")
    scale = _body_scale(source_doc, target_doc, source_human, target_human)
    source_hips = source_human.get("hips")
    out = []
    for clip in clips:
        channels = []
        for channel in clip["channels"]:
            mapped = mapping.get(channel["node"])
            if not mapped:
                continue
            dst, kind = mapped
            path = channel["path"]
            if kind == "humanoid" and path == "scale":
                continue
            times = list(channel["times"])
            values = [list(row) for row in channel["values"]]
            interpolation = channel["interpolation"]
            if kind == "humanoid" and interpolation == "CUBICSPLINE":
                values = [_key_value(values, index, interpolation) for index in range(len(times))]
                interpolation = "LINEAR"
            if kind == "humanoid" and path == "rotation":
                source_rest = _node_rotation(source_nodes, channel["node"])
                target_rest = _node_rotation(target_nodes, dst)
                turn = _quat_mul(target_rest, _quat_inv(source_rest))
                values = [_quat_norm(_quat_mul(turn, value)) for value in values]
            elif kind == "humanoid" and path == "translation" and channel["node"] == source_hips:
                source_rest = _node_translation(source_nodes, channel["node"])
                target_rest = _node_translation(target_nodes, dst)
                values = [
                    [target_rest[axis] + (value[axis] - source_rest[axis]) * scale for axis in range(3)]
                    for value in values
                ]
            elif kind == "humanoid" and path == "translation":
                continue
            channels.append(
                {
                    "node": dst,
                    "path": path,
                    "interpolation": interpolation,
                    "times": times,
                    "values": values,
                }
            )
        if channels:
            out.append({"name": clip["name"], "duration": clip["duration"], "channels": channels})
    if not out:
        raise MeshError("That animation does not match this rig.")
    return out


def _clip_time(duration: float, time: float, loop: bool) -> float:
    if duration <= 0:
        return 0.0
    if loop:
        if time < 0:
            return duration + (time % duration)
        if time > duration:
            return time % duration
        return time
    return max(0.0, min(duration, time))


def _is_quat(values: list[list[float]], interpolation: str) -> bool:
    sample = _key_value(values, 0, interpolation)
    return len(sample) == 4


def _key_value(values: list[list[float]], index: int, interpolation: str) -> list[float]:
    if interpolation == "CUBICSPLINE":
        return list(values[index * 3 + 1])
    return list(values[index])


def _cubic_point(values: list[list[float]], lo: int, t: float, dt: float) -> list[float]:
    start = values[lo * 3 + 1]
    out_tangent = values[lo * 3 + 2]
    end = values[(lo + 1) * 3 + 1]
    in_tangent = values[(lo + 1) * 3]
    t2 = t * t
    t3 = t2 * t
    h00 = 2 * t3 - 3 * t2 + 1
    h10 = t3 - 2 * t2 + t
    h01 = -2 * t3 + 3 * t2
    h11 = t3 - t2
    return [
        h00 * start[index] + h10 * dt * out_tangent[index] + h01 * end[index] + h11 * dt * in_tangent[index]
        for index in range(len(start))
    ]


def _node_rotation(nodes: list, index: int) -> list[float]:
    node = nodes[index] if isinstance(index, int) and 0 <= index < len(nodes) and isinstance(nodes[index], dict) else {}
    rotation = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    try:
        return _quat_norm([float(rotation[0]), float(rotation[1]), float(rotation[2]), float(rotation[3])])
    except (TypeError, ValueError, IndexError):
        return [0.0, 0.0, 0.0, 1.0]


def _node_translation(nodes: list, index: int) -> list[float]:
    node = nodes[index] if isinstance(index, int) and 0 <= index < len(nodes) and isinstance(nodes[index], dict) else {}
    translation = node.get("translation") or [0.0, 0.0, 0.0]
    try:
        return [float(translation[0]), float(translation[1]), float(translation[2])]
    except (TypeError, ValueError, IndexError):
        return [0.0, 0.0, 0.0]


def _body_scale(source_doc: dict, target_doc: dict, source_human: dict[str, int], target_human: dict[str, int]) -> float:
    source = _bone_span(source_doc, source_human)
    target = _bone_span(target_doc, target_human)
    if source < 1e-5:
        return 1.0
    ratio = target / source
    if ratio < 0.05 or ratio > 20:
        return 1.0
    return ratio


def _bone_span(document: dict, human: dict[str, int]) -> float:
    nodes = document.get("nodes") or []
    worlds = _world_matrices(nodes, document.get("scenes") or [])
    start = human.get("hips")
    end = human.get("head")
    if end is None:
        end = human.get("neck")
    if not isinstance(start, int) or not isinstance(end, int):
        return 1.0
    if start < 0 or end < 0 or start >= len(worlds) or end >= len(worlds):
        return 1.0
    dx = worlds[start][12] - worlds[end][12]
    dy = worlds[start][13] - worlds[end][13]
    dz = worlds[start][14] - worlds[end][14]
    return math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0


def _quat_mul(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]


def _quat_inv(quat: list[float]) -> list[float]:
    x, y, z, w = quat
    length = x * x + y * y + z * z + w * w or 1.0
    return [-x / length, -y / length, -z / length, w / length]


def _quat_norm(quat: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in quat[:4])) or 1.0
    return [value / length for value in quat[:4]]


def _quat_slerp(a: list[float], b: list[float], t: float) -> list[float]:
    start = _quat_norm(a)
    end = _quat_norm(b)
    dot = sum(start[index] * end[index] for index in range(4))
    if dot < 0:
        end = [-value for value in end]
        dot = -dot
    if dot > 0.9995:
        return _quat_norm([start[index] + (end[index] - start[index]) * t for index in range(4)])
    theta = math.acos(min(1.0, dot))
    sine = math.sin(theta)
    if abs(sine) < 1e-8:
        return start
    w1 = math.sin((1.0 - t) * theta) / sine
    w2 = math.sin(t * theta) / sine
    return [start[index] * w1 + end[index] * w2 for index in range(4)]


def _read_float_vectors(document: dict, blob: bytes, accessor_index: int) -> list[list[float]]:
    accessor, view, blob = _view(document, blob, accessor_index)
    if accessor.get("sparse"):
        raise MeshError("That animation uses a format Koma cannot read.")
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}.get(accessor.get("type"))
    if accessor.get("componentType") != 5126 or not width:
        raise MeshError("That animation uses a format Koma cannot read.")
    count = int(accessor.get("count") or 0)
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    stride = int(view.get("byteStride") or (4 * width))
    values = []
    for index in range(count):
        offset = start + index * stride
        if offset + 4 * width > len(blob):
            break
        values.append(list(struct.unpack_from(f"<{width}f", blob, offset)))
    return values


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
    skinned: set[int] = set()
    joint_nodes: set[int] = set()
    for index, node in enumerate(nodes):
        if isinstance(node, dict) and node.get("skin") is not None:
            skinned.add(index)
    for skin in document.get("skins") or []:
        if not isinstance(skin, dict):
            continue
        for joint in skin.get("joints") or []:
            if isinstance(joint, int):
                joint_nodes.add(joint)
    parent_of: dict[int, int] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for child in node.get("children") or []:
            if isinstance(child, int):
                parent_of[child] = index

    def ancestors(index: int) -> list[int]:
        found = []
        seen = set()
        while index in parent_of and index not in seen:
            seen.add(index)
            index = parent_of[index]
            found.append(index)
        return found

    baked_nodes: set[int] = set()
    for mesh_index, node_indices in users.items():
        meshes = document.get("meshes") or []
        if mesh_index < 0 or mesh_index >= len(meshes):
            continue
        if any(index in skinned or index in joint_nodes for index in node_indices):
            continue
        above = [parent for index in node_indices for parent in ancestors(index)]
        if any(parent in joint_nodes or parent in skinned for parent in above):
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
        baked_nodes.update(node_indices)
        baked_nodes.update(above)
    if not changed:
        return data
    for index in baked_nodes:
        node = nodes[index]
        if isinstance(node, dict) and index not in joint_nodes:
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
