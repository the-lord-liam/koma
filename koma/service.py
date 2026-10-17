"""Studio actions the window calls: plan, cast, panels, export."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from koma import prompts
from koma.ai import AIError, Client
from koma.compose import render_page
from koma.config import load_config, public_settings, save_config
from koma.formats import FormatError, catalog as format_catalog, project_pixels, resolve_format
from koma.layouts import LAYOUTS, LayoutError, assist_divide, place, reflow_after_delete, to_stored_line
from koma.media import media_url, save_image, unlink
from koma.mesh import NOT_INSTALLED, MeshError, command_available, generate_mesh, load_model_file, obj_from_glb, parse_glb
from koma.paths import data_dir, export_root, exports_dir, meshes_dir, previews_dir, safe_slug
from koma.prompts import (
    ANCHORS,
    ANGLES,
    BALLOONS,
    COMPOSITIONS,
    DEFAULT_STYLE,
    EXPRESSIONS,
    ILLUST_ASPECTS,
    ILLUST_STYLE,
    SHOTS,
    STORY_SYSTEM,
    build_panel,
    character_prompt,
    choice,
    extract_plan,
    illustration_prompt,
    location_prompt,
    plan_request,
    project_brief,
)
from koma.store import Store, new_id as new_mesh_id

BALLOON_IDS = [item["id"] for item in BALLOONS]
ANCHOR_IDS = [item["id"] for item in ANCHORS]


def _loads(text, fallback):
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def _clip(value, limit: int) -> str:
    return str(value or "")[:limit]


def summarize_plan(plan: dict) -> dict:
    chapters = []
    for chapter in plan.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapters.append(
            {
                "title": str(chapter.get("title") or "Chapter"),
                "synopsis": str(chapter.get("synopsis") or ""),
                "pages": len(chapter.get("pages") or []),
            }
        )
    cast = [
        str(person.get("name"))
        for person in plan.get("cast") or []
        if isinstance(person, dict) and person.get("name")
    ]
    places = [
        str(place.get("name"))
        for place in plan.get("locations") or []
        if isinstance(place, dict) and place.get("name")
    ]
    return {
        "title": str(plan.get("title") or ""),
        "logline": str(plan.get("logline") or ""),
        "cast": cast,
        "locations": places,
        "chapters": chapters,
    }


class Studio:
    def __init__(
        self,
        store: Store,
        client: Client | None = None,
        status: Callable[[str], None] | None = None,
        mesh_backend: Callable | None = None,
    ):
        self.store = store
        self.client = client or Client(lambda: load_config()["api_key"])
        self.status = status or (lambda _text: None)
        self.mesh_backend = mesh_backend
        self._undo: dict[str, list[dict]] = {}
        self._redo: dict[str, list[dict]] = {}
        self._history_ready: set[str] = set()

    def dispatch(self, method: str, params: dict | None):
        handler = ROUTES.get(method)
        if handler is None:
            raise ValueError(f"Unknown request: {method}")
        return handler(self, params or {})

    # --- views ---

    def _pack(self, project_id: str, **extra) -> dict:
        payload = {"tree": self._view(project_id)}
        payload.update(extra)
        return payload

    def _view(self, project_id: str) -> dict:
        project = self.store.get_project(project_id)
        self.store.ensure_volume(project_id)
        chapters = []
        for chapter in self.store.list_chapters(project_id):
            pages = []
            for page in self.store.list_pages(chapter["id"]):
                self._ensure_panels(page["id"], page["layout"])
                page = self.store.get_page(page["id"])
                count = _page_count(page)
                slots = _page_slots_ltr(page)
                panels = [
                    self._panel_out(panel)
                    for panel in self.store.list_panels(page["id"])
                    if panel["slot"] < count
                ]
                layout = page["layout"] if slots or page["layout"] in LAYOUTS else "classic"
                pages.append(
                    {
                        "id": page["id"],
                        "idx": page["idx"],
                        "layout": layout,
                        "notes": page["notes"],
                        "prompt": page.get("prompt") or "",
                        "characterIds": _loads(page.get("character_ids"), []),
                        "locationId": page.get("location_id") or None,
                        "refs": self._refs_out("page", page["id"], None),
                        "slots": place(slots, project["reading"]) if slots else None,
                        "panels": panels,
                    }
                )
            chapters.append(
                {
                    "id": chapter["id"],
                    "idx": chapter["idx"],
                    "title": chapter["title"],
                    "synopsis": chapter["synopsis"],
                    "beats": _loads(chapter["beats"], []),
                    "volumeId": chapter.get("volume_id") or "",
                    "pages": pages,
                }
            )
        pending = _loads(project.get("pending_plan"), None)
        project_out = {
            "id": project["id"],
            "title": project["title"],
            "premise": project["premise"],
            "logline": project["logline"],
            "genre": project["genre"],
            "tone": project["tone"],
            "artStyle": project["art_style"],
            "styleNotes": project["style_notes"],
            "reading": project["reading"] if project["reading"] in {"rtl", "ltr"} else "rtl",
            "updatedAt": project["updated_at"],
            "pendingPlan": summarize_plan(pending) if isinstance(pending, dict) else None,
        }
        project_out.update(_page_meta(project))
        return {
            "project": project_out,
            "characters": [self._character_out(row) for row in self.store.list_characters(project_id)],
            "locations": [self._location_out(row) for row in self.store.list_locations(project_id)],
            "volumes": [self._volume_out(row) for row in self.store.list_volumes(project_id)],
            "notes": [self._note_out(row) for row in self.store.list_notes(project_id)],
            "bots": [self._bot_out(row) for row in self.store.list_bots(project_id)],
            "botGroups": [self._group_out(row) for row in self.store.list_groups(project_id)],
            "chapters": chapters,
            "messages": [
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "createdAt": row["created_at"],
                }
                for row in self.store.list_messages(project_id)
            ],
        }

    def _character_out(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "personality": row["personality"],
            "appearance": row["appearance"],
            "wardrobe": row["wardrobe"],
            "primaryRefId": row.get("primary_ref_id"),
            "refs": self._refs_out("character", row["id"], row.get("primary_ref_id")),
            "meshes": self._meshes_out(row["id"]),
        }

    def _meshes_out(self, character_id: str) -> list[dict]:
        items = []
        for mesh in self.store.list_meshes(character_id):
            name = Path(mesh["glb_path"]).name
            items.append(
                {
                    "id": mesh["id"],
                    "prompt": mesh["prompt"],
                    "provider": mesh["provider"],
                    "source": mesh["source"],
                    "url": f"koma:///mesh/{name}",
                    "thumb": media_url(mesh.get("thumb_path")) if mesh.get("thumb_path") else "",
                    "createdAt": mesh["created_at"],
                }
            )
        return items

    def _location_out(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "primaryRefId": row.get("primary_ref_id"),
            "refs": self._refs_out("location", row["id"], row.get("primary_ref_id")),
        }

    def _refs_out(self, kind: str, owner_id: str, primary: str | None) -> list[dict]:
        items = []
        for ref in self.store.list_refs(kind, owner_id):
            items.append(
                {
                    "id": ref["id"],
                    "kind": ref["kind"],
                    "label": ref["label"],
                    "url": media_url(ref["image_path"]),
                    "createdAt": ref["created_at"],
                    "isPrimary": ref["id"] == primary,
                }
            )
        return items

    def _panel_out(self, panel: dict) -> dict:
        versions = []
        for version in self.store.list_versions(panel["id"])[:16]:
            versions.append(
                {
                    "id": version["id"],
                    "url": media_url(version["image_path"]),
                    "note": version["note"],
                    "createdAt": version["created_at"],
                    "current": version["id"] == panel.get("current_version_id"),
                }
            )
        return {
            "id": panel["id"],
            "slot": panel["slot"],
            "shot": panel["shot"],
            "angle": panel["angle"],
            "expression": panel["expression"],
            "composition": panel["composition"],
            "action": panel["action"],
            "dialogue": panel["dialogue"],
            "balloon": panel["balloon"],
            "balloonAnchor": panel["balloon_anchor"],
            "sfx": panel["sfx"],
            "extra": panel["extra"],
            "characterIds": _loads(panel["character_ids"], []),
            "locationId": panel.get("location_id"),
            "continuePrev": bool(panel.get("continue_prev")),
            "prompt": panel.get("prompt") or "",
            "objects": _objects_out(panel.get("objects")),
            "vibe": panel.get("vibe") or "",
            "refs": self._refs_out("panel", panel["id"], None),
            "imageUrl": media_url(panel.get("image_path")),
            "versions": versions,
        }

    def _volume_out(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "idx": row["idx"],
            "title": row["title"],
            "synopsis": row["synopsis"],
        }

    def _note_out(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "idx": row["idx"],
            "title": row["title"],
            "body": row["body"],
            "scope": row["scope"] if row["scope"] in {"story", "volume", "chapter", "page"} else "story",
            "scopeId": row.get("scope_id") or "",
            "updatedAt": row.get("updated_at"),
        }

    def _bot_out(self, row: dict) -> dict:
        return {"id": row["id"], "name": row["name"], "persona": row["persona"]}

    def _group_out(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "botIds": self.store.group_bot_ids(row["id"]),
        }

    def _project_list(self) -> list[dict]:
        items = []
        for row in self.store.list_projects():
            chapters = self.store.list_chapters(row["id"])
            items.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "logline": row["logline"],
                    "updatedAt": row["updated_at"],
                    "chapters": len(chapters),
                }
            )
        return items

    def _remember(self, page_id: str) -> None:
        self._ensure_history(page_id)
        stack = self._undo.setdefault(page_id, [])
        stack.append(self._capture_page(page_id))
        if len(stack) > 20:
            del stack[:-20]
        self._redo[page_id] = []
        self._save_history(page_id)

    def _capture_page(self, page_id: str) -> dict:
        page = self.store.get_page(page_id)
        panels = []
        for panel in self.store.list_panels(page_id):
            image = None
            path = panel.get("image_path") or ""
            if path and Path(path).is_file():
                image = Path(path).read_bytes()
            refs = []
            for ref in self.store.list_refs("panel", panel["id"]):
                ref_path = ref.get("image_path") or ""
                blob = Path(ref_path).read_bytes() if ref_path and Path(ref_path).is_file() else None
                refs.append(
                    {
                        "kind": ref.get("kind") or "reference",
                        "label": ref.get("label") or "",
                        "prompt": ref.get("prompt") or "",
                        "image": blob,
                    }
                )
            panels.append(
                {
                    "id": panel["id"],
                    "slot": int(panel["slot"]),
                    "shot": panel.get("shot") or "medium",
                    "angle": panel.get("angle") or "eye level",
                    "expression": panel.get("expression") or "neutral",
                    "composition": panel.get("composition") or "rule of thirds",
                    "action": panel.get("action") or "",
                    "dialogue": panel.get("dialogue") or "",
                    "balloon": panel.get("balloon") or "round",
                    "balloon_anchor": panel.get("balloon_anchor") or "tr",
                    "sfx": panel.get("sfx") or "",
                    "extra": panel.get("extra") or "",
                    "character_ids": panel.get("character_ids") or "[]",
                    "location_id": panel.get("location_id"),
                    "continue_prev": int(panel.get("continue_prev") or 0),
                    "prompt": panel.get("prompt") or "",
                    "objects": panel.get("objects") or "[]",
                    "vibe": panel.get("vibe") or "",
                    "image": image,
                    "refs": refs,
                }
            )
        return {
            "layout": page.get("layout") or "classic",
            "slots_json": page.get("slots_json") or "",
            "panels": panels,
        }

    def _restore_page(self, page_id: str, snap: dict) -> None:
        self.store.wipe_panels(page_id)
        slots_json = str(snap.get("slots_json") or "").strip()
        layout = snap.get("layout") or "classic"
        if slots_json:
            self.store.set_page_slots(page_id, slots_json)
            if layout != "custom":
                self.store.update_page(page_id, {"layout": layout}, {"layout"})
        else:
            self.store.update_page(page_id, {"layout": layout}, {"layout"})
            self.store.clear_page_slots(page_id)
        for item in sorted(snap.get("panels") or [], key=lambda row: int(row["slot"])):
            self.store.insert_panel(page_id, item)
            if item.get("image"):
                path = str(save_image(item["image"]))
                self.store.add_version(item["id"], path, "", "Restored")
            for ref in item.get("refs") or []:
                if not ref.get("image"):
                    continue
                self.store.create_ref(
                    "panel",
                    item["id"],
                    ref.get("kind") or "reference",
                    ref.get("label") or "",
                    str(save_image(ref["image"])),
                    ref.get("prompt") or "",
                )

    def page_undo(self, params: dict) -> dict:
        return self._step_history(params["pageId"], undo=True)

    def page_redo(self, params: dict) -> dict:
        return self._step_history(params["pageId"], undo=False)

    def _step_history(self, page_id: str, *, undo: bool) -> dict:
        self._ensure_history(page_id)
        page = self.store.get_page(page_id)
        chapter = self.store.get_chapter(page["chapter_id"])
        source = self._undo if undo else self._redo
        target = self._redo if undo else self._undo
        stack = source.get(page_id) or []
        if not stack:
            raise ValueError("Nothing to undo on this page." if undo else "Nothing to redo on this page.")
        current = self._capture_page(page_id)
        snap = stack.pop()
        target.setdefault(page_id, []).append(current)
        if len(target[page_id]) > 20:
            del target[page_id][:-20]
        self._restore_page(page_id, snap)
        self._save_history(page_id)
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def _history_root(self) -> Path:
        path = data_dir() / "history"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ensure_history(self, page_id: str) -> None:
        if page_id in self._history_ready:
            return
        self._history_ready.add(page_id)
        path = self._history_root() / f"{page_id}.json"
        if not path.is_file():
            self._undo.setdefault(page_id, [])
            self._redo.setdefault(page_id, [])
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._undo[page_id] = []
            self._redo[page_id] = []
            return
        if not isinstance(data, dict):
            self._undo[page_id] = []
            self._redo[page_id] = []
            return
        self._undo[page_id] = [self._snap_in(item) for item in (data.get("undo") or []) if isinstance(item, dict)][-20:]
        self._redo[page_id] = [self._snap_in(item) for item in (data.get("redo") or []) if isinstance(item, dict)][-20:]

    def _save_history(self, page_id: str) -> None:
        payload = {
            "undo": [self._snap_out(item) for item in (self._undo.get(page_id) or [])[-20:]],
            "redo": [self._snap_out(item) for item in (self._redo.get(page_id) or [])[-20:]],
        }
        path = self._history_root() / f"{page_id}.json"
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
            self._gc_blobs()
        except OSError:
            return

    def _drop_history(self, page_id: str, *, gc: bool = True) -> None:
        self._undo.pop(page_id, None)
        self._redo.pop(page_id, None)
        self._history_ready.discard(page_id)
        path = self._history_root() / f"{page_id}.json"
        try:
            path.unlink(missing_ok=True)
            if gc:
                self._gc_blobs()
        except OSError:
            return

    def _blob_put(self, blob: bytes | None) -> str:
        if not blob:
            return ""
        name = hashlib.sha256(blob).hexdigest()[:20] + ".bin"
        folder = self._history_root() / "blobs"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        if not target.exists():
            target.write_bytes(blob)
        return name

    def _blob_get(self, name: str) -> bytes | None:
        if not name or not isinstance(name, str):
            return None
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        folder = (self._history_root() / "blobs").resolve()
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            return None
        return path.read_bytes()

    def _snap_out(self, snap: dict) -> dict:
        panels = []
        for panel in snap.get("panels") or []:
            item = {key: value for key, value in panel.items() if key not in {"image", "refs"}}
            item["image"] = self._blob_put(panel.get("image"))
            item["refs"] = [
                {
                    "kind": ref.get("kind") or "reference",
                    "label": ref.get("label") or "",
                    "prompt": ref.get("prompt") or "",
                    "image": self._blob_put(ref.get("image")),
                }
                for ref in (panel.get("refs") or [])
            ]
            panels.append(item)
        return {
            "layout": snap.get("layout") or "classic",
            "slots_json": snap.get("slots_json") or "",
            "panels": panels,
        }

    def _snap_in(self, raw: dict) -> dict:
        panels = []
        for panel in raw.get("panels") or []:
            if not isinstance(panel, dict):
                continue
            item = dict(panel)
            item["image"] = self._blob_get(str(panel.get("image") or ""))
            refs = []
            for ref in panel.get("refs") or []:
                if not isinstance(ref, dict):
                    continue
                refs.append(
                    {
                        "kind": ref.get("kind") or "reference",
                        "label": ref.get("label") or "",
                        "prompt": ref.get("prompt") or "",
                        "image": self._blob_get(str(ref.get("image") or "")),
                    }
                )
            item["refs"] = refs
            panels.append(item)
        return {
            "layout": raw.get("layout") or "classic",
            "slots_json": raw.get("slots_json") or "",
            "panels": panels,
        }

    def _gc_blobs(self) -> None:
        root = self._history_root()
        used: set[str] = set()
        for path in root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            for stack in (data.get("undo") or [], data.get("redo") or []):
                if not isinstance(stack, list):
                    continue
                for snap in stack:
                    if not isinstance(snap, dict):
                        continue
                    for panel in snap.get("panels") or []:
                        if not isinstance(panel, dict):
                            continue
                        if panel.get("image"):
                            used.add(str(panel["image"]))
                        for ref in panel.get("refs") or []:
                            if isinstance(ref, dict) and ref.get("image"):
                                used.add(str(ref["image"]))
        folder = root / "blobs"
        if not folder.is_dir():
            return
        for blob in folder.iterdir():
            if blob.is_file() and blob.name not in used:
                blob.unlink(missing_ok=True)

    def _panel_has_work(self, panel: dict) -> bool:
        if str(panel.get("image_path") or "").strip():
            return True
        for key in ("dialogue", "sfx", "action", "prompt"):
            if str(panel.get(key) or "").strip():
                return True
        if self.store.list_versions(panel["id"]):
            return True
        return bool(self.store.list_refs("panel", panel["id"]))

    def _apply_named_layout(self, page: dict, layout_id: str, drop_extra: bool) -> None:
        count = _slot_count(layout_id)
        extras = [item for item in self.store.list_panels(page["id"]) if int(item["slot"]) >= count]
        worked = [item for item in extras if self._panel_has_work(item)]
        if worked and not drop_extra:
            noun = "panel" if len(worked) == 1 else "panels"
            raise ValueError(f"This layout removes {len(worked)} {noun} with art or lettering.")
        self._remember(page["id"])
        self.store.update_page(page["id"], {"layout": layout_id}, {"layout"})
        self.store.clear_page_slots(page["id"])
        for item in sorted(extras, key=lambda row: int(row["slot"]), reverse=True):
            self.store.delete_panel(item["id"])
        self._ensure_panels(page["id"], layout_id)

    def _ensure_panels(self, page_id: str, layout: str | None = None) -> None:
        page = self.store.get_page(page_id)
        slots = _page_slots_ltr(page)
        count = len(slots) if slots else _slot_count(layout or page.get("layout") or "")
        existing = {panel["slot"] for panel in self.store.list_panels(page_id)}
        for slot in range(count):
            if slot not in existing:
                self.store.create_panel(page_id, slot)

    def _identity_path(self, kind: str, owner: dict) -> str | None:
        primary = owner.get("primary_ref_id")
        if primary:
            try:
                return self.store.get_ref(primary)["image_path"]
            except KeyError:
                pass
        refs = self.store.list_refs(kind, owner["id"])
        preferred = "portrait" if kind == "character" else "plate"
        for ref in refs:
            if ref["kind"] == preferred:
                return ref["image_path"]
        return refs[0]["image_path"] if refs else None

    def _panel_bundle(self, panel_id: str):
        panel = self.store.get_panel(panel_id)
        page = self.store.get_page(panel["page_id"])
        chapter = self.store.get_chapter(page["chapter_id"])
        project = self.store.get_project(chapter["project_id"])
        characters = self._cast_refs(project["id"], _loads(panel["character_ids"], []))
        location = self._location_ref(project["id"], panel.get("location_id"))
        slots = _page_slots_ltr(page)
        if slots:
            slot = slots[min(max(int(panel["slot"]), 0), len(slots) - 1)]
        else:
            layout = page["layout"] if page["layout"] in LAYOUTS else "classic"
            slot = LAYOUTS[layout][1][min(panel["slot"], len(LAYOUTS[layout][1]) - 1)]
        return panel, page, chapter, project, characters, location, slot

    def _imagine(self, prompt: str, aspect: str, refs: list[str]) -> str:
        for ref in refs:
            if not Path(ref).is_file():
                raise ValueError("A reference image is missing from disk. Generate it again.")
        settings = load_config()
        data = self.client.imagine(
            prompt,
            aspect,
            refs,
            resolution=settings["resolution"],
            quality=settings.get("quality") or "low",
        )
        return str(save_image(data))

    def _cast_refs(self, project_id: str, character_ids: list) -> list[dict]:
        by_id = {row["id"]: row for row in self.store.list_characters(project_id)}
        characters = []
        for character_id in character_ids:
            person = by_id.get(character_id)
            if not person:
                continue
            person = dict(person)
            person["ref_path"] = self._identity_path("character", person)
            characters.append(person)
        return characters

    def _location_ref(self, project_id: str, location_id: str | None) -> dict | None:
        if not location_id:
            return None
        try:
            location = dict(self.store.get_location(location_id))
        except KeyError:
            return None
        if location["project_id"] != project_id:
            return None
        location["ref_path"] = self._identity_path("location", location)
        return location

    def _known_character_ids(self, project_id: str, raw) -> list[str]:
        if isinstance(raw, str):
            raw = _loads(raw, [])
        if not isinstance(raw, list):
            return []
        known = {row["id"] for row in self.store.list_characters(project_id)}
        chosen = []
        for item in raw:
            if item in known and item not in chosen:
                chosen.append(item)
        return chosen

    # --- routes ---

    def bootstrap(self, _params: dict) -> dict:
        from koma.layouts import catalog

        return {
            "settings": public_settings(),
            "layouts": catalog(),
            "formats": format_catalog(),
            "vocab": prompts.vocab(),
            "projects": self._project_list(),
            "mangaBoards": self._manga_boards(),
            "illustrations": self._illust_list(),
            "dataDir": str(data_dir()),
        }

    def settings_save(self, params: dict) -> dict:
        updates = {}
        if params.get("clearKey"):
            updates["api_key"] = ""
        elif isinstance(params.get("apiKey"), str) and params["apiKey"].strip():
            updates["api_key"] = params["apiKey"].strip()
        if "quality" in params:
            updates["quality"] = params["quality"]
        if "resolution" in params:
            updates["resolution"] = params["resolution"]
        provider = params.get("meshProvider", params.get("mesh_provider"))
        if provider in {"local", "meshy", "tripo"}:
            updates["mesh_provider"] = provider
        if "meshCommand" in params or "mesh_command" in params:
            updates["mesh_command"] = params.get("meshCommand", params.get("mesh_command"))
        if params.get("clearMeshyKey"):
            updates["meshy_key"] = ""
        elif isinstance(params.get("meshyKey"), str) and params["meshyKey"].strip():
            updates["meshy_key"] = params["meshyKey"].strip()
        if params.get("clearTripoKey"):
            updates["tripo_key"] = ""
        elif isinstance(params.get("tripoKey"), str) and params["tripoKey"].strip():
            updates["tripo_key"] = params["tripoKey"].strip()
        return {"settings": public_settings(save_config(updates))}

    def project_create(self, params: dict) -> dict:
        title = _clip(params.get("title"), 120).strip() or "Untitled manga"
        premise = _clip(params.get("premise"), 8000).strip()
        project = self.store.create_project(title, premise, DEFAULT_STYLE)
        return self._pack(project["id"], projects=self._project_list())

    def project_open(self, params: dict) -> dict:
        return self._pack(params["id"])

    def project_update(self, params: dict) -> dict:
        project_id = params["id"]
        self.store.get_project(project_id)
        mapping = {
            "title": "title",
            "premise": "premise",
            "logline": "logline",
            "genre": "genre",
            "tone": "tone",
            "artStyle": "art_style",
            "styleNotes": "style_notes",
            "reading": "reading",
        }
        fields = {}
        for source, dest in mapping.items():
            if source not in params:
                continue
            value = _clip(params[source], 8000).strip() if source != "title" else _clip(params[source], 120).strip()
            if source == "reading" and value not in {"rtl", "ltr"}:
                continue
            if source == "title" and not value:
                raise ValueError("Name the manga.")
            fields[dest] = value
        self.store.update_project(project_id, fields, set(fields))
        extra = {"mangaBoards": self._manga_boards()} if "title" in fields else {}
        return self._pack(project_id, **extra)

    def project_delete(self, params: dict) -> dict:
        page_ids = self._page_ids(params["id"])
        self.store.delete_project(params["id"])
        for page_id in page_ids:
            self._drop_preview(page_id)
            self._drop_history(page_id, gc=False)
        self._gc_blobs()
        return self._gallery_payload()

    def story_send(self, params: dict) -> dict:
        project_id = params["projectId"]
        project = self.store.get_project(project_id)
        mode = params.get("mode") or "note"
        chapters_n = _bounded(params.get("chapters"), 3, 1, 12)
        pages_n = _bounded(params.get("pagesPer"), 4, 1, 16)
        if mode == "plan":
            user_text = plan_request(project, chapters_n, pages_n)
            visible = f"Plan the story ({chapters_n} chapters, about {pages_n} pages each)."
        else:
            user_text = _clip(params.get("message"), 8000).strip()
            visible = user_text
            if not user_text:
                raise ValueError("Write a note for the editor.")
        self.store.add_message(project_id, "user", visible)
        brief = project_brief(
            project,
            self.store.list_characters(project_id),
            self.store.list_locations(project_id),
            self.store.list_chapters(project_id),
        )
        messages = [{"role": "system", "content": STORY_SYSTEM + "\n\nCurrent project:\n" + brief}]
        history = self.store.list_messages(project_id)[-10:]
        for item in history:
            messages.append({"role": item["role"], "content": item["content"]})
        if mode == "plan":
            messages[-1] = {"role": "user", "content": user_text}
        self.status("Planning the story…")
        reply = self.client.complete(messages)
        prose, plan = extract_plan(reply)
        self.store.add_message(project_id, "assistant", prose[:20000])
        if plan:
            self.store.update_project(project_id, {"pending_plan": json.dumps(plan)}, {"pending_plan"})
        else:
            self.store.touch(project_id)
        return self._pack(project_id, reply=prose, hasPlan=bool(plan))

    def story_apply(self, params: dict) -> dict:
        project_id = params["projectId"]
        mode = params.get("mode") or "replace"
        if mode not in {"replace", "append"}:
            raise ValueError("Choose replace or append.")
        project = self.store.get_project(project_id)
        plan = _loads(project.get("pending_plan"), None)
        if not isinstance(plan, dict):
            raise ValueError("There is no plan to apply.")
        self.status("Applying the plan…")
        with self.store.batch():
            meta = {}
            for source, dest in (
                ("title", "title"),
                ("logline", "logline"),
                ("genre", "genre"),
                ("tone", "tone"),
                ("art_style", "art_style"),
                ("style_notes", "style_notes"),
            ):
                value = plan.get(source)
                if isinstance(value, str) and value.strip():
                    meta[dest] = value.strip()[:8000]
            if meta.get("title"):
                meta["title"] = meta["title"][:120]
            if meta:
                self.store.update_project(project_id, meta, set(meta))
            self._upsert_cast(project_id, plan.get("cast") or [])
            self._upsert_locations(project_id, plan.get("locations") or [])
            if mode == "replace":
                for chapter in self.store.list_chapters(project_id):
                    self.store.delete_chapter(chapter["id"])
            names = {row["name"].casefold(): row["id"] for row in self.store.list_characters(project_id)}
            places = {row["name"].casefold(): row["id"] for row in self.store.list_locations(project_id)}
            for chapter in plan.get("chapters") or []:
                if not isinstance(chapter, dict):
                    continue
                beats = chapter.get("beats") if isinstance(chapter.get("beats"), list) else []
                row = self.store.create_chapter(
                    project_id,
                    _clip(chapter.get("title"), 160).strip() or "Chapter",
                    _clip(chapter.get("synopsis"), 4000),
                    json.dumps([str(beat) for beat in beats if str(beat).strip()]),
                )
                for page in chapter.get("pages") or []:
                    if not isinstance(page, dict):
                        continue
                    layout = page.get("layout") if page.get("layout") in LAYOUTS else "classic"
                    page_row = self.store.create_page(row["id"], layout, _clip(page.get("notes"), 2000))
                    specs = page.get("panels") if isinstance(page.get("panels"), list) else []
                    for slot in range(_slot_count(layout)):
                        spec = specs[slot] if slot < len(specs) and isinstance(specs[slot], dict) else {}
                        character_ids = []
                        for name in spec.get("characters") or []:
                            found = names.get(str(name).strip().casefold())
                            if found and found not in character_ids:
                                character_ids.append(found)
                        place = places.get(str(spec.get("location") or "").strip().casefold())
                        dialogue = _clip(spec.get("dialogue"), 2000)
                        self.store.create_panel(
                            page_row["id"],
                            slot,
                            {
                                "shot": choice(spec.get("shot"), SHOTS, "medium"),
                                "angle": choice(spec.get("angle"), ANGLES, "eye level"),
                                "expression": choice(spec.get("expression"), EXPRESSIONS, "neutral"),
                                "composition": choice(spec.get("composition"), COMPOSITIONS, "rule of thirds"),
                                "action": _clip(spec.get("action"), 2000),
                                "dialogue": dialogue,
                                "balloon": "round" if dialogue.strip() else "none",
                                "sfx": _clip(spec.get("sfx"), 80),
                                "character_ids": json.dumps(character_ids),
                                "location_id": place,
                            },
                        )
            self.store.update_project(project_id, {"pending_plan": None}, {"pending_plan"})
        return self._pack(project_id)

    def story_discard(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.update_project(project_id, {"pending_plan": None}, {"pending_plan"})
        return self._pack(project_id)

    def _upsert_cast(self, project_id: str, cast) -> None:
        existing = {row["name"].casefold(): row for row in self.store.list_characters(project_id)}
        allowed = {"name", "role", "personality", "appearance", "wardrobe"}
        for item in cast if isinstance(cast, list) else []:
            if not isinstance(item, dict):
                continue
            name = _clip(item.get("name"), 80).strip()
            if not name:
                continue
            fields = {key: _clip(item.get(key), 4000).strip() for key in allowed}
            fields["name"] = name
            found = existing.get(name.casefold())
            if found:
                self.store.update_character(found["id"], fields, allowed)
            else:
                created = self.store.create_character(project_id, name)
                self.store.update_character(created["id"], fields, allowed)
                existing[name.casefold()] = created

    def _upsert_locations(self, project_id: str, places) -> None:
        existing = {row["name"].casefold(): row for row in self.store.list_locations(project_id)}
        for item in places if isinstance(places, list) else []:
            if not isinstance(item, dict):
                continue
            name = _clip(item.get("name"), 80).strip()
            if not name:
                continue
            description = _clip(item.get("description"), 4000).strip()
            found = existing.get(name.casefold())
            if found:
                if description:
                    self.store.update_location(found["id"], {"name": name, "description": description}, {"name", "description"})
            else:
                created = self.store.create_location(project_id, name)
                if description:
                    self.store.update_location(created["id"], {"description": description}, {"description"})
                existing[name.casefold()] = created

    def chapter_create(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.get_project(project_id)
        volume_id = str(params.get("volumeId") or "").strip() or None
        self.store.create_chapter(
            project_id,
            _clip(params.get("title"), 160).strip() or f"Chapter {self.store._next_idx('chapters', 'project_id', project_id) + 1}",
            volume_id=volume_id,
        )
        return self._pack(project_id)

    def chapter_update(self, params: dict) -> dict:
        chapter = self.store.get_chapter(params["id"])
        fields = {}
        if "title" in params:
            title = _clip(params.get("title"), 160).strip()
            if not title:
                raise ValueError("Name the chapter.")
            fields["title"] = title
        if "synopsis" in params:
            fields["synopsis"] = _clip(params.get("synopsis"), 4000)
        if "beats" in params:
            beats = params["beats"]
            if isinstance(beats, str):
                beats = [line.strip() for line in beats.splitlines() if line.strip()]
            if not isinstance(beats, list):
                beats = []
            fields["beats"] = json.dumps([_clip(beat, 400) for beat in beats])
        if "volumeId" in params and str(params.get("volumeId") or "").strip():
            volume = self.store.get_volume(str(params["volumeId"]))
            if volume["project_id"] != chapter["project_id"]:
                raise ValueError("That volume belongs to another book.")
            fields["volume_id"] = volume["id"]
        self.store.update_chapter(chapter["id"], fields, {"title", "synopsis", "beats", "volume_id"})
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def chapter_delete(self, params: dict) -> dict:
        chapter = self.store.get_chapter(params["id"])
        page_ids = [page["id"] for page in self.store.list_pages(chapter["id"])]
        self.store.delete_chapter(chapter["id"])
        for page_id in page_ids:
            self._drop_preview(page_id)
            self._drop_history(page_id, gc=False)
        self._gc_blobs()
        return self._pack(chapter["project_id"])

    def chapter_duplicate(self, params: dict) -> dict:
        source = self.store.get_chapter(params["id"])
        title = _clip(f"{(source.get('title') or 'Chapter').strip()} copy", 160)
        created = self.store.create_chapter(
            source["project_id"],
            title,
            source.get("synopsis") or "",
            beats=source.get("beats") or "[]",
            volume_id=source.get("volume_id") or None,
        )
        for page in self.store.list_pages(source["id"]):
            self.page_duplicate({"pageId": page["id"], "chapterId": created["id"]})
        chapters = [chapter["id"] for chapter in self.store.list_chapters(source["project_id"])]
        chapters.remove(created["id"])
        chapters.insert(chapters.index(source["id"]) + 1, created["id"])
        self.store.reorder("chapters", "project_id", source["project_id"], chapters)
        self.store.touch(source["project_id"])
        return self._pack(source["project_id"], chapterId=created["id"])

    def chapter_move(self, params: dict) -> dict:
        chapter = self.store.get_chapter(params["id"])
        self.store.move("chapters", "project_id", chapter["id"], _direction(params.get("direction")))
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def library_search(self, params: dict) -> dict:
        query = _clip(params.get("query"), 80).strip().casefold()
        if not query:
            return {"hits": []}
        hits = []
        for project in self.store.list_projects():
            title = project.get("title") or ""
            if query in title.casefold():
                hits.append(
                    {
                        "kind": "title",
                        "projectId": project["id"],
                        "projectTitle": title,
                        "label": title,
                        "detail": "Manga",
                    }
                )
            if len(hits) >= 40:
                return {"hits": hits}
            for person in self.store.list_characters(project["id"]):
                hay = " ".join(
                    str(person.get(key) or "")
                    for key in ("name", "role", "personality", "appearance", "wardrobe")
                )
                if query not in hay.casefold():
                    continue
                hits.append(
                    {
                        "kind": "cast",
                        "projectId": project["id"],
                        "projectTitle": title,
                        "characterId": person["id"],
                        "label": person.get("name") or "Character",
                        "detail": title,
                    }
                )
                if len(hits) >= 40:
                    return {"hits": hits}
            for note in self.store.list_notes(project["id"]):
                hay = f"{note.get('title') or ''} {note.get('body') or ''}"
                if query not in hay.casefold():
                    continue
                hits.append(
                    {
                        "kind": "note",
                        "projectId": project["id"],
                        "projectTitle": title,
                        "noteId": note["id"],
                        "label": note.get("title") or "Note",
                        "detail": title,
                    }
                )
                if len(hits) >= 40:
                    return {"hits": hits}
            for chapter in self.store.list_chapters(project["id"]):
                chapter_title = chapter.get("title") or ""
                if query in chapter_title.casefold():
                    hits.append(
                        {
                            "kind": "chapter",
                            "projectId": project["id"],
                            "projectTitle": title,
                            "chapterId": chapter["id"],
                            "label": chapter_title,
                            "detail": title,
                        }
                    )
                if len(hits) >= 40:
                    return {"hits": hits}
                for page in self.store.list_pages(chapter["id"]):
                    for panel in self.store.list_panels(page["id"]):
                        dialogue = panel.get("dialogue") or ""
                        if query not in dialogue.casefold():
                            continue
                        snippet = " ".join(dialogue.split())
                        if len(snippet) > 80:
                            snippet = snippet[:77] + "..."
                        hits.append(
                            {
                                "kind": "dialogue",
                                "projectId": project["id"],
                                "projectTitle": title,
                                "chapterId": chapter["id"],
                                "pageId": page["id"],
                                "panelId": panel["id"],
                                "label": snippet or dialogue,
                                "detail": f"{title} · {chapter_title}",
                            }
                        )
                        if len(hits) >= 40:
                            return {"hits": hits}
        return {"hits": hits}

    def page_create(self, params: dict) -> dict:
        chapter = self.store.get_chapter(params["chapterId"])
        layout = params.get("layout") if params.get("layout") in LAYOUTS else "classic"
        page = self.store.create_page(chapter["id"], layout, "")
        self._ensure_panels(page["id"], layout)
        return self._pack(chapter["project_id"])

    def page_update(self, params: dict) -> dict:
        page = self.store.get_page(params["id"])
        chapter = self.store.get_chapter(page["chapter_id"])
        fields = {}
        if "notes" in params:
            fields["notes"] = _clip(params.get("notes"), 2000)
        if "prompt" in params:
            fields["prompt"] = _clip(params.get("prompt"), 2000)
        if "characterIds" in params:
            fields["character_ids"] = json.dumps(
                self._known_character_ids(chapter["project_id"], params["characterIds"])
            )
        if "locationId" in params:
            location_id = params.get("locationId") or None
            if location_id:
                location = self.store.get_location(location_id)
                if location["project_id"] != chapter["project_id"]:
                    location_id = None
            fields["location_id"] = location_id
        layout_id = params.get("layout") if params.get("layout") in LAYOUTS else None
        self.store.update_page(
            page["id"],
            fields,
            {"notes", "layout", "prompt", "character_ids", "location_id"},
        )
        if layout_id and (layout_id != page["layout"] or str(page.get("slots_json") or "").strip()):
            self._apply_named_layout(page, layout_id, bool(params.get("dropExtra")))
        elif layout_id:
            self._ensure_panels(page["id"], layout_id)
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def page_delete(self, params: dict) -> dict:
        page = self.store.get_page(params["id"])
        chapter = self.store.get_chapter(page["chapter_id"])
        self.store.delete_page(page["id"])
        self._drop_preview(page["id"])
        self._drop_history(page["id"])
        return self._pack(chapter["project_id"])

    def page_move(self, params: dict) -> dict:
        page = self.store.get_page(params["id"])
        chapter = self.store.get_chapter(page["chapter_id"])
        self.store.move("pages", "chapter_id", page["id"], _direction(params.get("direction")))
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def page_reorder(self, params: dict) -> dict:
        chapter = self.store.get_chapter(params["chapterId"])
        ids = [str(item) for item in (params.get("ids") or [])]
        self.store.reorder("pages", "chapter_id", chapter["id"], ids)
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def page_duplicate(self, params: dict) -> dict:
        source = self.store.get_page(params["pageId"])
        chapter = self.store.get_chapter(source["chapter_id"])
        dest_id = str(params.get("chapterId") or chapter["id"])
        dest = self.store.get_chapter(dest_id)
        if dest["project_id"] != chapter["project_id"]:
            raise ValueError("That chapter belongs to another book.")
        chapter = dest
        slots_json = source.get("slots_json") or ""
        if str(slots_json).strip():
            layout = source["layout"] or "custom"
        else:
            layout = source["layout"] if source["layout"] in LAYOUTS else "classic"
        created = self.store.create_page(chapter["id"], layout, source.get("notes") or "", slots_json)
        self.store.update_page(
            created["id"],
            {
                "prompt": source.get("prompt") or "",
                "character_ids": source.get("character_ids") or "[]",
                "location_id": source.get("location_id"),
            },
            {"prompt", "character_ids", "location_id"},
        )
        for ref in self.store.list_refs("page", source["id"]):
            path = ref.get("image_path")
            if not path or not Path(path).is_file():
                continue
            copied_path = str(save_image(Path(path).read_bytes()))
            self.store.create_ref(
                "page",
                created["id"],
                ref.get("kind") or "reference",
                ref.get("label") or "",
                copied_path,
                ref.get("prompt") or "",
            )
        if dest["id"] == source["chapter_id"]:
            ordered = [page["id"] for page in self.store.list_pages(chapter["id"]) if page["id"] != created["id"]]
            ordered.insert(ordered.index(source["id"]) + 1, created["id"])
            self.store.reorder("pages", "chapter_id", chapter["id"], ordered)
        self._ensure_panels(created["id"], layout)
        count = _page_count(source)
        originals = {
            panel["slot"]: panel
            for panel in self.store.list_panels(source["id"])
            if panel["slot"] < count
        }
        script = {
            "shot",
            "angle",
            "expression",
            "composition",
            "action",
            "dialogue",
            "balloon",
            "balloon_anchor",
            "sfx",
            "extra",
            "character_ids",
            "location_id",
            "continue_prev",
            "prompt",
            "objects",
            "vibe",
        }
        copies = []
        for panel in self.store.list_panels(created["id"]):
            original = originals.get(panel["slot"])
            if original is None:
                continue
            self.store.update_panel(
                panel["id"],
                {
                    "shot": original["shot"],
                    "angle": original["angle"],
                    "expression": original["expression"],
                    "composition": original["composition"],
                    "action": original["action"],
                    "dialogue": original["dialogue"],
                    "balloon": original["balloon"],
                    "balloon_anchor": original["balloon_anchor"],
                    "sfx": original["sfx"],
                    "extra": original["extra"],
                    "character_ids": original["character_ids"],
                    "location_id": original.get("location_id"),
                    "continue_prev": int(original.get("continue_prev") or 0),
                    "prompt": original.get("prompt") or "",
                    "objects": original.get("objects") or "[]",
                    "vibe": original.get("vibe") or "",
                },
                script,
            )
            for ref in self.store.list_refs("panel", original["id"]):
                ref_path = ref.get("image_path")
                if not ref_path or not Path(ref_path).is_file():
                    continue
                self.store.create_ref(
                    "panel",
                    panel["id"],
                    ref.get("kind") or "reference",
                    ref.get("label") or "",
                    str(save_image(Path(ref_path).read_bytes())),
                    ref.get("prompt") or "",
                )
            path = original.get("image_path")
            if path and Path(path).is_file():
                copies.append((panel["id"], path, original.get("current_version_id")))
        for panel_id, path, version_id in copies:
            prompt = ""
            if version_id:
                try:
                    prompt = self.store.get_version(version_id).get("prompt") or ""
                except KeyError:
                    prompt = ""
            self.store.add_version(panel_id, str(save_image(Path(path).read_bytes())), prompt, "Copied")
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"], pageId=created["id"])

    def panel_update(self, params: dict) -> dict:
        panel, page, chapter, project, _characters, _location, _slot = self._panel_bundle(params["id"])
        fields = {}
        if "shot" in params:
            fields["shot"] = choice(params.get("shot"), SHOTS, panel["shot"])
        if "angle" in params:
            fields["angle"] = choice(params.get("angle"), ANGLES, panel["angle"])
        if "expression" in params:
            fields["expression"] = choice(params.get("expression"), EXPRESSIONS, panel["expression"])
        if "composition" in params:
            fields["composition"] = choice(params.get("composition"), COMPOSITIONS, panel["composition"])
        if "balloon" in params:
            fields["balloon"] = params["balloon"] if params["balloon"] in BALLOON_IDS else panel["balloon"]
        if "balloonAnchor" in params:
            anchor = params["balloonAnchor"]
            fields["balloon_anchor"] = anchor if anchor in ANCHOR_IDS else panel["balloon_anchor"]
        for source, dest, limit in (
            ("action", "action", 2000),
            ("dialogue", "dialogue", 2000),
            ("sfx", "sfx", 80),
            ("extra", "extra", 2000),
            ("prompt", "prompt", 2000),
            ("vibe", "vibe", 80),
        ):
            if source in params:
                fields[dest] = _clip(params.get(source), limit)
        if "objects" in params:
            fields["objects"] = json.dumps(_clean_objects(params.get("objects")))
        if "characterIds" in params:
            fields["character_ids"] = json.dumps(self._known_character_ids(project["id"], params["characterIds"]))
        if "locationId" in params:
            location_id = params.get("locationId") or None
            if location_id:
                location = self.store.get_location(location_id)
                if location["project_id"] != project["id"]:
                    location_id = None
            fields["location_id"] = location_id
        if "continuePrev" in params:
            fields["continue_prev"] = 1 if params.get("continuePrev") else 0
        if any(key in params for key in ("dialogue", "balloon", "balloonAnchor", "sfx")):
            self._remember(page["id"])
        self.store.update_panel(panel["id"], fields, set(fields))
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def panel_delete(self, params: dict) -> dict:
        panel, page, _chapter, project, _characters, _location, _slot = self._panel_bundle(params["id"])
        visible = self._panel_rows(page)
        if len(visible) < 2:
            raise ValueError("A page needs at least one panel.")
        self._remember(page["id"])
        slots = _page_slots_ltr(page)
        if slots is None:
            layout = page["layout"] if page["layout"] in LAYOUTS else "classic"
            slots = [dict(item) for item in LAYOUTS[layout][1]]
        index = int(panel["slot"])
        if 0 <= index < len(slots):
            slots = reflow_after_delete(slots, index)
        self.store.delete_panel(panel["id"])
        self.store.set_page_slots(page["id"], json.dumps(slots))
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def panel_move(self, params: dict) -> dict:
        panel, page, _chapter, project, _characters, _location, _slot = self._panel_bundle(params["id"])
        rows = self._panel_rows(page)
        ids = [item["id"] for item in rows]
        if panel["id"] not in ids:
            return self._pack(project["id"])
        index = ids.index(panel["id"])
        swap = index + _direction(params.get("direction"))
        if swap < 0 or swap >= len(rows):
            return self._pack(project["id"])
        self._remember(page["id"])
        first = rows[index]
        second = rows[swap]
        first_slot = int(first["slot"])
        second_slot = int(second["slot"])
        self.store.update_panel(first["id"], {"slot": -1}, {"slot"})
        self.store.update_panel(second["id"], {"slot": first_slot}, {"slot"})
        self.store.update_panel(first["id"], {"slot": second_slot}, {"slot"})
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def panel_reorder(self, params: dict) -> dict:
        page = self.store.get_page(params["pageId"])
        chapter = self.store.get_chapter(page["chapter_id"])
        rows = self._panel_rows(page)
        current = [item["id"] for item in rows]
        ids = [str(item) for item in (params.get("ids") or [])]
        if len(ids) != len(set(ids)) or sorted(ids) != sorted(current):
            raise ValueError("That order does not match the panels on this page.")
        if ids == current:
            return self._pack(chapter["project_id"])
        self._remember(page["id"])
        slots = [int(item["slot"]) for item in rows]
        for offset, panel_id in enumerate(ids):
            self.store.update_panel(panel_id, {"slot": -1 - offset}, {"slot"})
        for slot, panel_id in zip(slots, ids):
            self.store.update_panel(panel_id, {"slot": slot}, {"slot"})
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def panel_preview(self, params: dict) -> dict:
        built, _project = self._panel_build(params["id"])
        return {
            "prompt": built["prompt"],
            "aspect": built["aspect"],
            "refCount": len(built["refs"]),
            "warnings": built["warnings"],
        }

    def panel_draw(self, params: dict) -> dict:
        _panel, _page, _chapter, project, _characters, _location, _slot = self._panel_bundle(params["id"])
        self.status("Drawing the panel…")
        self._draw_panel(params["id"])
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def panel_draw_empty(self, params: dict) -> dict:
        if params.get("pageId"):
            page = self.store.get_page(params["pageId"])
            chapter = self.store.get_chapter(page["chapter_id"])
            pages = [page]
            scope = "page"
        elif params.get("chapterId"):
            chapter = self.store.get_chapter(params["chapterId"])
            pages = self.store.list_pages(chapter["id"])
            scope = "chapter"
        else:
            raise ValueError("Choose a page or a chapter.")
        project = self.store.get_project(chapter["project_id"])
        panel_ids = []
        for page in pages:
            count = _page_count(page)
            for panel in self.store.list_panels(page["id"]):
                if panel["slot"] < count and not panel.get("image_path"):
                    panel_ids.append(panel["id"])
        if not panel_ids:
            where = "page" if scope == "page" else "chapter"
            return self._pack(
                project["id"],
                drawn=0,
                total=0,
                warning=f"Every panel in this {where} already has a drawing.",
            )
        drawn = 0
        warning = None
        page_imagine = bool(params.get("imagine")) and scope == "page"
        for index, panel_id in enumerate(panel_ids, start=1):
            self.status(f"Drawing panel {index} of {len(panel_ids)}…")
            try:
                self._draw_panel(panel_id, page_imagine=page_imagine)
                drawn += 1
            except (AIError, ValueError, OSError) as error:
                warning = f"Stopped after {drawn} of {len(panel_ids)} panels. {error}"
                break
        self.store.touch(project["id"])
        return self._pack(project["id"], drawn=drawn, total=len(panel_ids), warning=warning)

    def _draw_panel(self, panel_id: str, *, page_imagine: bool = False) -> None:
        built, _project = self._panel_build(panel_id, page_imagine=page_imagine)
        path = self._imagine(built["prompt"], built["aspect"], built["refs"])
        self.store.add_version(panel_id, path, built["prompt"], "Drawn")

    def _panel_build(
        self,
        panel_id: str,
        *,
        revise: str | None = None,
        sketch: str | None = None,
        page_imagine: bool = False,
    ) -> tuple[dict, dict]:
        panel, page, _chapter, project, characters, location, slot = self._panel_bundle(panel_id)
        if page_imagine:
            panel = dict(panel)
            if not (panel.get("prompt") or "").strip():
                panel["prompt"] = page.get("prompt") or ""
            if not characters:
                characters = self._cast_refs(project["id"], _loads(page.get("character_ids"), []))
            if location is None:
                location = self._location_ref(project["id"], page.get("location_id"))
        previous = None
        if panel.get("continue_prev") and not revise and not sketch:
            previous = self._previous_image(page, panel)
        extra = []
        panel_refs = self.store.list_refs("panel", panel_id)
        panel_kinds = {ref.get("kind") for ref in panel_refs}
        attached = list(panel_refs)
        if page_imagine:
            for ref in self.store.list_refs("page", page["id"]):
                if ref.get("kind") not in panel_kinds:
                    attached.append(ref)
        for ref in attached:
            label = "background" if ref.get("kind") == "background" else "reference image"
            extra.append((ref["image_path"], label))
        instruction = revise
        if sketch and not instruction:
            instruction = (
                "Refine this rough sketch into a finished manga panel. "
                "Keep the pose, framing, and what is drawn. Clean ink lines and screentone."
            )
        page_w, page_h = project_pixels(project)
        built = build_panel(
            _project_prompt(project),
            panel,
            slot,
            characters,
            location,
            revise=instruction,
            current_image=sketch or panel.get("image_path"),
            previous_image=previous,
            page_aspect=(page_w / page_h) if page_h else None,
            extra_refs=extra,
        )
        if panel.get("continue_prev") and not revise and not sketch and not previous:
            built["warnings"].append("Match previous is on, but no earlier panel has a drawing yet.")
        return built, project

    def _previous_image(self, page: dict, panel: dict) -> str | None:
        """Last drawn panel before this one, on this page or the page before it."""
        earlier = [
            item
            for item in self._panel_rows(page)
            if item["slot"] < panel["slot"] and item.get("image_path") and Path(item["image_path"]).is_file()
        ]
        if earlier:
            return earlier[-1]["image_path"]
        chapter_pages = self.store.list_pages(page["chapter_id"])
        for previous_page in reversed([item for item in chapter_pages if item["idx"] < page["idx"]]):
            drawn = [
                item
                for item in self._panel_rows(previous_page)
                if item.get("image_path") and Path(item["image_path"]).is_file()
            ]
            if drawn:
                return drawn[-1]["image_path"]
        return None

    def panel_revise(self, params: dict) -> dict:
        instruction = _clip(params.get("instruction"), 2000).strip()
        if not instruction:
            raise ValueError("Say what to change.")
        built, project = self._panel_build(params["id"], revise=instruction)
        self.status("Revising the panel…")
        path = self._imagine(built["prompt"], built["aspect"], built["refs"])
        self.store.add_version(params["id"], path, built["prompt"], instruction[:80])
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def panel_use_version(self, params: dict) -> dict:
        panel = self.store.get_panel(params["panelId"])
        self.store.use_version(panel["id"], params["versionId"])
        project_id = self.store.get_chapter(self.store.get_page(panel["page_id"])["chapter_id"])["project_id"]
        self.store.touch(project_id)
        return self._pack(project_id)

    def character_create(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.get_project(project_id)
        name = _clip(params.get("name"), 80).strip() or "New character"
        self.store.create_character(project_id, name)
        return self._pack(project_id)

    def character_update(self, params: dict) -> dict:
        character = self.store.get_character(params["id"])
        fields = {}
        for key, limit in (
            ("name", 80),
            ("role", 120),
            ("personality", 2000),
            ("appearance", 4000),
            ("wardrobe", 2000),
        ):
            if key in params:
                value = _clip(params.get(key), limit).strip() if key == "name" else _clip(params.get(key), limit)
                if key == "name" and not value:
                    raise ValueError("Name the character.")
                fields[key] = value
        self.store.update_character(character["id"], fields, set(fields))
        self.store.touch(character["project_id"])
        return self._pack(character["project_id"])

    def character_delete(self, params: dict) -> dict:
        character = self.store.get_character(params["id"])
        project_id = character["project_id"]
        self.store.delete_character(character["id"])
        self._strip_character(project_id, character["id"])
        return self._pack(project_id)

    def _strip_character(self, project_id: str, character_id: str) -> None:
        for chapter in self.store.list_chapters(project_id):
            for page in self.store.list_pages(chapter["id"]):
                page_ids = _loads(page.get("character_ids"), [])
                if character_id in page_ids:
                    page_ids = [item for item in page_ids if item != character_id]
                    self.store.update_page(
                        page["id"],
                        {"character_ids": json.dumps(page_ids)},
                        {"character_ids"},
                    )
                for panel in self.store.list_panels(page["id"]):
                    ids = _loads(panel["character_ids"], [])
                    if character_id in ids:
                        ids = [item for item in ids if item != character_id]
                        self.store.update_panel(panel["id"], {"character_ids": json.dumps(ids)}, {"character_ids"})

    def character_generate(self, params: dict) -> dict:
        kind = params.get("kind") or "portrait"
        if kind not in {"portrait", "turnaround", "expressions", "poses"}:
            raise ValueError("Unknown reference type.")
        character = self.store.get_character(params["id"])
        project = self.store.get_project(character["project_id"])
        ref_path = self._identity_path("character", character)
        has_ref = bool(ref_path)
        label = _clip(params.get("label"), 200)
        prompt, aspect = character_prompt(project, character, kind, label, has_ref)
        self.status(f"Drawing {character['name']}…")
        path = self._imagine(prompt, aspect, [ref_path] if has_ref else [])
        ref = self.store.create_ref("character", character["id"], kind, label, path, prompt)
        if kind == "portrait" and not character.get("primary_ref_id"):
            self.store.update_character(character["id"], {"primary_ref_id": ref["id"]}, {"primary_ref_id"})
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def character_revise(self, params: dict) -> dict:
        instruction = _clip(params.get("instruction"), 2000).strip()
        if not instruction:
            raise ValueError("Say what to change.")
        ref = self.store.get_ref(params["refId"])
        if ref["owner_kind"] != "character":
            raise ValueError("That reference is not a character.")
        character = self.store.get_character(ref["owner_id"])
        project = self.store.get_project(character["project_id"])
        prompt = (
            f"<IMAGE_0> is {character['name']}. {instruction} "
            f"Keep the same person unless the note says otherwise. "
            f"Style: {project['art_style'] or DEFAULT_STYLE}. "
            "No written text, letters, speech balloons, captions, or sound effects."
        )
        self.status(f"Revising {character['name']}…")
        path = self._imagine(prompt, "2:3" if ref["kind"] == "portrait" else "16:9", [ref["image_path"]])
        created = self.store.create_ref("character", character["id"], ref["kind"], ref["label"], path, prompt)
        if character.get("primary_ref_id") == ref["id"]:
            self.store.update_character(character["id"], {"primary_ref_id": created["id"]}, {"primary_ref_id"})
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def character_primary(self, params: dict) -> dict:
        ref = self.store.get_ref(params["refId"])
        if ref["owner_kind"] != "character":
            raise ValueError("That reference is not a character.")
        character = self.store.get_character(ref["owner_id"])
        self.store.update_character(character["id"], {"primary_ref_id": ref["id"]}, {"primary_ref_id"})
        self.store.touch(character["project_id"])
        return self._pack(character["project_id"])

    def character_mesh(self, params: dict) -> dict:
        character = self.store.get_character(params["id"])
        project = self.store.get_project(character["project_id"])
        source = params.get("source") or "prompt"
        if source not in {"prompt", "refs"}:
            raise ValueError("Choose a prompt or reference images.")
        prompt = self._mesh_prompt(character, _clip(params.get("prompt"), 2000))
        settings = load_config()
        provider = settings.get("mesh_provider") or "local"
        if provider == "local" and self.mesh_backend is None and not command_available(settings):
            raise MeshError(NOT_INSTALLED)
        images = self._mesh_images(character, source, provider, project, prompt)
        self.status(f"Building a 3D model of {character['name']}…")
        if self.mesh_backend is not None:
            glb = self.mesh_backend(provider, prompt, images, self.status, settings)
        else:
            glb = generate_mesh(provider, prompt, images, self.status, settings)
        if not isinstance(glb, bytes) or not glb.startswith(b"glTF"):
            raise MeshError("The 3D backend did not return a GLB file.")
        path = meshes_dir() / f"{new_mesh_id()}.glb"
        path.write_bytes(glb)
        self.store.create_mesh(character["id"], prompt, provider, source, str(path))
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def character_mesh_import(self, params: dict) -> dict:
        character = self.store.get_character(params["id"])
        source = Path(str(params.get("path") or "")).expanduser()
        if not source.is_file():
            raise ValueError("Choose a GLB, VRM, or OBJ file.")
        resolved = source.resolve()
        roots = [Path.home().resolve(), Path(tempfile.gettempdir()).resolve(), data_dir().resolve()]
        if not any(resolved == root or root in resolved.parents for root in roots):
            raise ValueError("Choose a model inside your home folder.")
        if resolved.stat().st_size > 80_000_000:
            raise ValueError("That model is larger than 80 MB.")
        if resolved.stat().st_size == 0:
            raise ValueError("That model file is empty.")
        try:
            glb = load_model_file(resolved)
        except MeshError as error:
            raise ValueError(str(error)) from None
        if not isinstance(glb, bytes) or not glb.startswith(b"glTF"):
            raise ValueError("That file is not a GLB Koma can read.")
        try:
            parse_glb(glb)
        except MeshError as error:
            raise ValueError(str(error)) from None
        path = meshes_dir() / f"{new_mesh_id()}.glb"
        path.write_bytes(glb)
        self.store.create_mesh(character["id"], resolved.name[:200], "import", "file", str(path))
        self.store.touch(character["project_id"])
        return self._pack(character["project_id"])

    def character_mesh_delete(self, params: dict) -> dict:
        mesh = self.store.get_mesh(params["id"])
        character = self.store.get_character(mesh["character_id"])
        self.store.delete_mesh(mesh["id"])
        self.store.touch(character["project_id"])
        return self._pack(character["project_id"])

    def character_mesh_export(self, params: dict) -> dict:
        mesh = self.store.get_mesh(params["id"])
        character = self.store.get_character(mesh["character_id"])
        project = self.store.get_project(character["project_id"])
        fmt = params.get("format") or "glb"
        if fmt not in {"glb", "obj"}:
            raise ValueError("Export GLB or OBJ.")
        source = Path(mesh["glb_path"])
        if not source.is_file():
            raise ValueError("That model file is missing. Generate it again.")
        data = source.read_bytes()
        folder = exports_dir(project["title"])
        stem = f"{safe_slug(character['name'])}-model"
        if fmt == "glb":
            dest = folder / f"{stem}.glb"
            dest.write_bytes(data)
        else:
            dest = folder / f"{stem}.obj"
            dest.write_bytes(obj_from_glb(data))
        return {"path": str(dest)}

    def character_mesh_bytes(self, params: dict) -> dict:
        mesh = self.store.get_mesh(params["id"])
        path = Path(mesh["glb_path"])
        if not path.is_file():
            raise ValueError("That model file is missing. Generate it again.")
        data = path.read_bytes()
        total = len(data)
        if total > 80_000_000:
            raise ValueError("That model is too large to preview here. Export it instead.")
        offset = int(params.get("offset") or 0)
        if offset < 0 or offset > total:
            raise ValueError("That model could not be read.")
        if "offset" not in params and total <= 12_000_000:
            return {"glb": base64.b64encode(data).decode("ascii"), "offset": 0, "total": total}
        piece = data[offset : offset + 1_500_000]
        return {"glb": base64.b64encode(piece).decode("ascii"), "offset": offset, "total": total}

    def character_mesh_thumb(self, params: dict) -> dict:
        mesh = self.store.get_mesh(params["id"])
        character = self.store.get_character(mesh["character_id"])
        self._store_mesh_thumb(mesh, _png_bytes(params.get("png")))
        self.store.touch(character["project_id"])
        return self._pack(character["project_id"])

    def character_mesh_shot(self, params: dict) -> dict:
        """Save the current 3D view as a pose reference, and on a panel when one is open."""
        mesh = self.store.get_mesh(params["id"])
        character = self.store.get_character(mesh["character_id"])
        panel_id = str(params.get("panelId") or "").strip()
        panel = None
        if panel_id:
            try:
                panel = self.store.get_panel(panel_id)
            except KeyError:
                raise ValueError("Open a panel, then save this angle onto it.") from None
            page = self.store.get_page(panel["page_id"])
            chapter = self.store.get_chapter(page["chapter_id"])
            if chapter["project_id"] != character["project_id"]:
                raise ValueError("That panel is in another book.")
        data = _png_bytes(params.get("png"))
        image_path = str(save_image(data))
        self.store.create_ref(
            "character",
            character["id"],
            "poses",
            "3D angle",
            image_path,
            "Saved from the 3D view.",
        )
        self._store_mesh_thumb(mesh, data)
        if panel is not None:
            self.store.create_ref(
                "panel",
                panel["id"],
                "reference",
                "3D angle",
                str(save_image(data)),
                "Saved from the 3D view.",
            )
        self.store.touch(character["project_id"])
        return self._pack(character["project_id"])

    def _store_mesh_thumb(self, mesh: dict, data: bytes) -> None:
        self.store.set_mesh_thumb(mesh["id"], str(save_image(_thumb_png(data))))

    def _mesh_prompt(self, character: dict, typed: str) -> str:
        typed = typed.strip()
        if typed:
            return typed
        parts = [character["name"]]
        if character.get("appearance"):
            parts.append(character["appearance"])
        if character.get("wardrobe"):
            parts.append(character["wardrobe"])
        return ". ".join(part for part in parts if part)

    def _mesh_images(self, character: dict, source: str, provider: str, project: dict, prompt: str) -> list[str]:
        if source == "refs":
            images = self._ordered_mesh_refs(character)
            if not images:
                raise ValueError("Draw or import a portrait before generating a model from references.")
            return images[:1]
        if provider != "local":
            return []
        identity = self._identity_path("character", character)
        if identity:
            return [identity]
        self.status(f"Drawing a reference of {character['name']}…")
        draw = (
            f"Full-body character reference of {character['name']}, front view, plain background. "
            f"{character.get('appearance') or ''} {character.get('wardrobe') or ''} {prompt}. "
            f"Style: {project.get('art_style') or DEFAULT_STYLE}. "
            "No written text, letters, speech balloons, captions, or sound effects."
        )
        path = self._imagine(draw, "2:3", [])
        ref = self.store.create_ref("character", character["id"], "portrait", "3D reference", path, draw)
        if not character.get("primary_ref_id"):
            self.store.update_character(character["id"], {"primary_ref_id": ref["id"]}, {"primary_ref_id"})
        return [path]

    def _ordered_mesh_refs(self, character: dict) -> list[str]:
        refs = self.store.list_refs("character", character["id"])
        primary = character.get("primary_ref_id")
        ordered = [ref for ref in refs if ref["id"] == primary]
        ordered.extend(ref for ref in refs if ref["id"] != primary and ref["kind"] in {"portrait", "turnaround"})
        ordered.extend(ref for ref in refs if ref not in ordered)
        images = []
        for ref in ordered:
            path = ref.get("image_path")
            if path and path not in images:
                images.append(path)
        return images

    def character_delete_ref(self, params: dict) -> dict:
        ref = self.store.get_ref(params["refId"])
        if ref["owner_kind"] != "character":
            raise ValueError("That reference is not a character.")
        character = self.store.get_character(ref["owner_id"])
        self.store.delete_ref(ref["id"])
        if character.get("primary_ref_id") == ref["id"]:
            remaining = self.store.list_refs("character", character["id"])
            nxt = next((item["id"] for item in remaining if item["kind"] == "portrait"), None)
            if nxt is None and remaining:
                nxt = remaining[0]["id"]
            self.store.update_character(character["id"], {"primary_ref_id": nxt}, {"primary_ref_id"})
        self.store.touch(character["project_id"])
        return self._pack(character["project_id"])

    def location_create(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.get_project(project_id)
        name = _clip(params.get("name"), 80).strip() or "New place"
        self.store.create_location(project_id, name)
        return self._pack(project_id)

    def location_update(self, params: dict) -> dict:
        location = self.store.get_location(params["id"])
        fields = {}
        if "name" in params:
            name = _clip(params.get("name"), 80).strip()
            if name:
                fields["name"] = name
        if "description" in params:
            fields["description"] = _clip(params.get("description"), 4000)
        self.store.update_location(location["id"], fields, set(fields))
        self.store.touch(location["project_id"])
        return self._pack(location["project_id"])

    def location_delete(self, params: dict) -> dict:
        location = self.store.get_location(params["id"])
        self.store.delete_location(location["id"])
        return self._pack(location["project_id"])

    def location_generate(self, params: dict) -> dict:
        location = self.store.get_location(params["id"])
        project = self.store.get_project(location["project_id"])
        ref_path = self._identity_path("location", location)
        instruction = _clip(params.get("instruction"), 2000).strip()
        if instruction and not ref_path:
            raise ValueError("Draw the place before revising it.")
        prompt, aspect = location_prompt(project, location, bool(ref_path), instruction)
        self.status(f"Drawing {location['name']}…")
        path = self._imagine(prompt, aspect, [ref_path] if ref_path else [])
        ref = self.store.create_ref("location", location["id"], "plate", "", path, prompt)
        if not location.get("primary_ref_id") or instruction:
            self.store.update_location(location["id"], {"primary_ref_id": ref["id"]}, {"primary_ref_id"})
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def import_ref(self, params: dict) -> dict:
        owner = params.get("owner")
        if owner not in {"character", "location", "panel", "page"}:
            raise ValueError("Choose a character, a place, a page, or a panel.")
        source = Path(str(params.get("path") or "")).expanduser()
        if not source.is_file():
            raise ValueError("Choose an image file.")
        resolved = source.resolve()
        roots = [Path.home().resolve(), Path(tempfile.gettempdir()).resolve(), data_dir().resolve()]
        if not any(resolved == root or root in resolved.parents for root in roots):
            raise ValueError("Choose an image inside your home folder.")
        if resolved.stat().st_size > 25_000_000:
            raise ValueError("That image is larger than 25 MB.")
        try:
            with Image.open(resolved) as image:
                image.load()
                if max(image.size) > 8000:
                    raise ValueError("That image is too large.")
        except ValueError:
            raise
        except Exception:
            raise ValueError("That file is not an image Koma can read.") from None
        saved = str(save_image(resolved.read_bytes()))
        if owner in {"panel", "page"}:
            if owner == "panel":
                panel = self.store.get_panel(params["id"])
                page = self.store.get_page(panel["page_id"])
                owner_id = panel["id"]
            else:
                page = self.store.get_page(params["id"])
                owner_id = page["id"]
            chapter = self.store.get_chapter(page["chapter_id"])
            kind = params.get("kind") if params.get("kind") in {"reference", "background"} else "reference"
            self.store.create_ref(owner, owner_id, kind, "imported", saved, "Imported from disk.")
            self.store.touch(chapter["project_id"])
            return self._pack(chapter["project_id"])
        if owner == "character":
            character = self.store.get_character(params["id"])
            kind = params.get("kind") if params.get("kind") in {"portrait", "turnaround", "expressions", "poses"} else "portrait"
            ref = self.store.create_ref("character", character["id"], kind, "imported", saved, "Imported from disk.")
            if kind == "portrait" and not character.get("primary_ref_id"):
                self.store.update_character(character["id"], {"primary_ref_id": ref["id"]}, {"primary_ref_id"})
            self.store.touch(character["project_id"])
            return self._pack(character["project_id"])
        location = self.store.get_location(params["id"])
        ref = self.store.create_ref("location", location["id"], "plate", "imported", saved, "Imported from disk.")
        if not location.get("primary_ref_id"):
            self.store.update_location(location["id"], {"primary_ref_id": ref["id"]}, {"primary_ref_id"})
        self.store.touch(location["project_id"])
        return self._pack(location["project_id"])

    def page_sheet(self, params: dict) -> dict:
        page = self.store.get_page(params["pageId"])
        chapter = self.store.get_chapter(page["chapter_id"])
        project = self.store.get_project(chapter["project_id"])
        return {"pageId": page["id"], "url": self._preview(project, chapter, page)}

    def chapter_sheets(self, params: dict) -> dict:
        chapter = self.store.get_chapter(params["chapterId"])
        project = self.store.get_project(chapter["project_id"])
        pages = self.store.list_pages(chapter["id"])
        if not pages:
            raise ValueError("This chapter has no pages yet.")
        sheets = []
        for page in pages:
            sheets.append(
                {
                    "pageId": page["id"],
                    "url": self._preview(project, chapter, page),
                    "index": page["idx"] + 1,
                    "layout": page["layout"],
                }
            )
        return {"chapterId": chapter["id"], "sheets": sheets}

    def export_page(self, params: dict) -> dict:
        try:
            page = self.store.get_page(params["pageId"])
            chapter = self.store.get_chapter(page["chapter_id"])
            project = self.store.get_project(chapter["project_id"])
            path = self._render(project, chapter, page)
            return {"path": str(path), "folder": str(path.parent)}
        except OSError as error:
            raise ValueError("Koma cannot write the export. The folder may be read-only.") from error

    def export_chapter(self, params: dict) -> dict:
        try:
            chapter = self.store.get_chapter(params["chapterId"])
            project = self.store.get_project(chapter["project_id"])
            pages = self.store.list_pages(chapter["id"])
            if not pages:
                raise ValueError("This chapter has no pages yet.")
            folder = exports_dir(project["title"])
            paths = [self._render(project, chapter, page) for page in pages]
            if params.get("format") == "cbz":
                cbz_path = folder / f"ch{chapter['idx'] + 1:02d}.cbz"
                with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for index, path in enumerate(paths, start=1):
                        archive.write(path, f"{index:03d}{Path(path).suffix or '.png'}")
                return {"path": str(cbz_path), "folder": str(folder)}
            if params.get("format") == "pdf":
                images = [Image.open(path).convert("RGB") for path in paths]
                pdf_path = folder / f"ch{chapter['idx'] + 1:02d}.pdf"
                images[0].save(pdf_path, save_all=True, append_images=images[1:])
                for image in images:
                    image.close()
                return {"path": str(pdf_path), "folder": str(folder)}
            return {"path": str(paths[-1]), "folder": str(folder)}
        except OSError as error:
            raise ValueError("Koma cannot write the export. The folder may be read-only.") from error

    def _panel_rows(self, page: dict) -> list[dict]:
        count = _page_count(page)
        return [panel for panel in self.store.list_panels(page["id"]) if panel["slot"] < count]

    def _paint(self, project: dict, chapter: dict, page: dict, target: Path) -> Path:
        slots = _page_slots_ltr(page)
        layout = page["layout"] if slots or page["layout"] in LAYOUTS else "classic"
        width, height = project_pixels(project)
        label = f"{chapter['idx'] + 1}-{page['idx'] + 1}"
        rows = self._panel_rows(page)
        if slots is None:
            return render_page(layout, project["reading"], rows, label, target, width, height)
        return render_page(layout, project["reading"], rows, label, target, width, height, slots)

    def _render(self, project: dict, chapter: dict, page: dict) -> Path:
        folder = exports_dir(project["title"])
        target = folder / f"ch{chapter['idx'] + 1:02d}-p{page['idx'] + 1:02d}.png"
        return self._paint(project, chapter, page, target)

    def _preview(self, project: dict, chapter: dict, page: dict) -> str:
        target = previews_dir() / f"{page['id']}.png"
        self._paint(project, chapter, page, target)
        return f"koma:///preview/{page['id']}.png"

    def _drop_preview(self, page_id: str) -> None:
        unlink(str(previews_dir() / f"{page_id}.png"))

    def _page_ids(self, project_id: str) -> list[str]:
        try:
            self.store.get_project(project_id)
        except KeyError:
            return []
        ids = []
        for chapter in self.store.list_chapters(project_id):
            ids.extend(page["id"] for page in self.store.list_pages(chapter["id"]))
        return ids

    def canvas_gallery(self, _params: dict) -> dict:
        return self._gallery_payload()

    def canvas_prepare(self, params: dict) -> dict:
        title = _clip(params.get("title"), 120).strip()
        if not title:
            raise ValueError("Name the manga.")
        try:
            spec = resolve_format(
                str(params.get("kind") or ""),
                str(params.get("preset") or ""),
                str(params.get("quality") or ""),
                width=params.get("width"),
                height=params.get("height"),
                unit=str(params.get("unit") or "mm"),
                dpi=params.get("dpi"),
            )
        except FormatError as error:
            raise ValueError(str(error)) from error
        if params.get("create"):
            project_id = self.store.create_project(title, "", DEFAULT_STYLE)["id"]
        else:
            project_id = str(params.get("projectId") or "")
            if not project_id:
                raise ValueError("Choose a manga first.")
            self.store.get_project(project_id)
        self.store.set_page_format(
            project_id,
            {
                "title": title,
                "reading": spec["reading"],
                "page_kind": spec["kind"],
                "page_preset": spec["preset"],
                "trim_w_mm": spec["w_mm"],
                "trim_h_mm": spec["h_mm"],
                "page_dpi": spec["dpi"],
                "page_quality": spec["quality"],
                "page_unit": spec["unit"],
            },
        )
        self._ensure_manga_page(project_id)
        return self._pack(project_id, **self._gallery_payload())

    def canvas_open_manga(self, params: dict) -> dict:
        if params.get("create"):
            project_id = self.store.create_project("Untitled", "", DEFAULT_STYLE)["id"]
        else:
            project_id = str(params.get("projectId") or "")
            if project_id:
                self.store.get_project(project_id)
            else:
                projects = self.store.list_projects()
                project_id = projects[0]["id"] if projects else self.store.create_project("Untitled", "", DEFAULT_STYLE)["id"]
        self._ensure_manga_page(project_id)
        return self._pack(project_id, **self._gallery_payload())

    def canvas_open_illust(self, params: dict) -> dict:
        if params.get("create") or not str(params.get("id") or ""):
            if params.get("create") or not self.store.list_illustrations():
                illust_id = self.store.create_illustration("Illustration", ILLUST_STYLE)["id"]
            else:
                illust_id = str(params.get("id") or "") or self.store.list_illustrations()[0]["id"]
        else:
            illust_id = str(params["id"])
            self.store.get_illustration(illust_id)
        return self._gallery_payload(illustId=illust_id)

    def illust_update(self, params: dict) -> dict:
        illust = self.store.get_illustration(params["id"])
        fields = {}
        if "title" in params:
            title = _clip(params.get("title"), 120).strip()
            if title:
                fields["title"] = title
        if "prompt" in params:
            fields["prompt"] = _clip(params.get("prompt"), 4000)
        if "artStyle" in params:
            fields["art_style"] = _clip(params.get("artStyle"), 2000)
        if "aspect" in params and params["aspect"] in ILLUST_ASPECTS:
            fields["aspect"] = params["aspect"]
        self.store.update_illustration(illust["id"], fields)
        return self._gallery_payload(illustId=illust["id"])

    def illust_draw(self, params: dict) -> dict:
        self.illust_update(params)
        illust = self.store.get_illustration(params["id"])
        if not illust["prompt"].strip():
            raise ValueError("Describe the illustration first.")
        prompt = illustration_prompt(illust)
        self.status("Drawing the illustration…")
        path = self._imagine(prompt, illust["aspect"] if illust["aspect"] in ILLUST_ASPECTS else "2:3", [])
        self.store.add_illust_version(illust["id"], path, prompt, "Drawn")
        return self._gallery_payload(illustId=illust["id"])

    def illust_revise(self, params: dict) -> dict:
        instruction = _clip(params.get("instruction"), 2000).strip()
        if not instruction:
            raise ValueError("Say what to change.")
        illust = self.store.get_illustration(params["id"])
        if not illust.get("image_path"):
            raise ValueError("Draw the illustration before revising it.")
        prompt = illustration_prompt(illust, revise=instruction)
        self.status("Revising the illustration…")
        path = self._imagine(prompt, illust["aspect"] if illust["aspect"] in ILLUST_ASPECTS else "2:3", [illust["image_path"]])
        self.store.add_illust_version(illust["id"], path, prompt, instruction[:80])
        return self._gallery_payload(illustId=illust["id"])

    def illust_use_version(self, params: dict) -> dict:
        illust = self.store.get_illustration(params["id"])
        self.store.use_illust_version(illust["id"], params["versionId"])
        return self._gallery_payload(illustId=illust["id"])

    def illust_delete(self, params: dict) -> dict:
        illust = self.store.get_illustration(params["id"])
        self.store.delete_illustration(illust["id"])
        return self._gallery_payload()

    def illust_export(self, params: dict) -> dict:
        illust = self.store.get_illustration(params["id"])
        source = illust.get("image_path") or ""
        if not source or not Path(source).is_file():
            raise ValueError("Draw the illustration before exporting it.")
        folder = exports_dir("Illustrations")
        target = folder / f"{safe_slug(illust['title'])}-{illust['id'][:6]}{Path(source).suffix or '.png'}"
        shutil.copyfile(source, target)
        return {"path": str(target), "folder": str(folder)}

    def _ensure_manga_page(self, project_id: str) -> None:
        chapters = self.store.list_chapters(project_id)
        if not chapters:
            chapters = [self.store.create_chapter(project_id, "Chapter 1")]
        if any(self.store.list_pages(chapter["id"]) for chapter in chapters):
            return
        page = self.store.create_page(chapters[0]["id"], "classic", "")
        self._ensure_panels(page["id"], "classic")

    def _gallery_payload(self, **extra) -> dict:
        payload = {
            "projects": self._project_list(),
            "mangaBoards": self._manga_boards(),
            "illustrations": self._illust_list(),
        }
        payload.update(extra)
        return payload

    def _manga_boards(self) -> list[dict]:
        boards = []
        for project in self.store.list_projects():
            page = self.store.latest_worked_page(project["id"])
            layout = "classic"
            panels = []
            if page:
                slots = _page_slots_ltr(page)
                layout = page["layout"] if slots or page["layout"] in LAYOUTS else "classic"
                for panel in self._panel_rows(page):
                    panels.append({"slot": panel["slot"], "imageUrl": media_url(panel.get("image_path"))})
            board = {
                "projectId": project["id"],
                "title": project["title"],
                "layout": layout,
                "reading": project["reading"] if project["reading"] in {"rtl", "ltr"} else "rtl",
                "panels": panels,
            }
            board.update(_page_meta(project))
            boards.append(board)
        return boards

    def _illust_list(self) -> list[dict]:
        return [self._illust_out(row) for row in self.store.list_illustrations()]

    def _illust_out(self, row: dict) -> dict:
        versions = []
        for version in self.store.list_illust_versions(row["id"])[:16]:
            versions.append(
                {
                    "id": version["id"],
                    "url": media_url(version["image_path"]),
                    "note": version["note"],
                    "current": version["id"] == row.get("current_version_id"),
                }
            )
        aspect = row["aspect"] if row["aspect"] in ILLUST_ASPECTS else "2:3"
        return {
            "id": row["id"],
            "title": row["title"],
            "prompt": row["prompt"],
            "aspect": aspect,
            "artStyle": row["art_style"],
            "imageUrl": media_url(row.get("image_path")),
            "versions": versions,
        }

    def page_divide(self, params: dict) -> dict:
        page = self.store.get_page(params["id"])
        chapter = self.store.get_chapter(page["chapter_id"])
        project = self.store.get_project(chapter["project_id"])
        slots = _page_slots_ltr(page)
        if slots is None:
            layout = page["layout"] if page["layout"] in LAYOUTS else "classic"
            slots = [dict(item) for item in LAYOUTS[layout][1]]
        try:
            x = float(params.get("x"))
            y = float(params.get("y"))
            angle = float(params.get("angle"))
        except (TypeError, ValueError):
            raise ValueError("Place the cut on the page.") from None
        stored_x, stored_y, stored_angle = to_stored_line(x, y, angle, project["reading"])
        try:
            divided, index, axis, straightened = assist_divide(slots, stored_x, stored_y, stored_angle)
        except LayoutError as error:
            raise ValueError(str(error)) from error
        self.store.set_page_slots(page["id"], json.dumps(divided))
        self.store.shift_slots_after(page["id"], index)
        self.store.create_panel(page["id"], index + 1)
        self._ensure_panels(page["id"])
        self.store.touch(project["id"])
        return self._pack(project["id"], straightened=straightened, axis=axis)

    def page_suggest(self, params: dict) -> dict:
        page = self.store.get_page(params.get("id") or params.get("pageId"))
        chapter = self.store.get_chapter(page["chapter_id"])
        project = self.store.get_project(chapter["project_id"])
        beats = _loads(chapter.get("beats"), [])
        if not isinstance(beats, list):
            beats = []
        actions = [
            (panel.get("action") or "").strip()
            for panel in self.store.list_panels(page["id"])
            if (panel.get("action") or "").strip()
        ]
        layout_id = None
        from_grok = False
        needs_key = False
        catalog = ", ".join(LAYOUTS)
        reply, needs_key = self._complete(
            [
                {"role": "system", "content": "Reply with one layout id and nothing else."},
                {
                    "role": "user",
                    "content": (
                        f"Book: {project.get('title') or ''}. Chapter: {chapter.get('title') or ''}. "
                        f"Beats: {', '.join(str(beat) for beat in beats)}. "
                        f"Panel actions: {'; '.join(actions)}. "
                        f"Choose one of: {catalog}."
                    ),
                },
            ]
        )
        if reply:
            token = reply.strip().split()[0].strip(".,`\"'")
            if token in LAYOUTS:
                layout_id = token
                from_grok = True
        if layout_id is None:
            layout_id = _suggest_layout(beats, actions)
        self._apply_named_layout(page, layout_id, bool(params.get("dropExtra")))
        self.store.touch(project["id"])
        return self._pack(
            project["id"],
            layoutId=layout_id,
            label=LAYOUTS[layout_id][0],
            fromGrok=from_grok,
            needsKey=needs_key,
        )

    def panel_separate(self, params: dict) -> dict:
        panel, _page, _chapter, project, characters, location, _slot = self._panel_bundle(params["id"])
        names = [person["name"] for person in characters]
        place_name = location["name"] if location else ""
        things: list[dict] = []
        cast = ", ".join(names) or "none"
        reply, _needs_key = self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Separate the panel into characters, things, and the background. "
                        'Reply with JSON only: {"things": ["object"]}. No prose.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Action: {panel.get('action') or ''}. "
                        f"Cast already chosen: {cast}. Background: {place_name or 'unset'}."
                    ),
                },
            ]
        )
        parsed = _json_object(reply or "")
        if parsed:
            raw = parsed.get("things") if "things" in parsed else parsed.get("objects")
            if isinstance(raw, list):
                things = _clean_objects(raw)
        if not things:
            things = _clean_objects(_loads(panel.get("objects"), [])) or _guess_things(panel.get("action") or "")
        self.store.update_panel(panel["id"], {"objects": json.dumps(things)}, {"objects"})
        self.store.touch(project["id"])
        return self._pack(project["id"], things=things, characters=names, background=place_name)

    def panel_letter(self, params: dict) -> dict:
        panel, _page, _chapter, project, _characters, _location, _slot = self._panel_bundle(params["id"])
        dialogue = (panel.get("dialogue") or "").strip()
        balloon = panel.get("balloon") if panel.get("balloon") in BALLOON_IDS else "round"
        anchor = panel.get("balloon_anchor") if panel.get("balloon_anchor") in ANCHOR_IDS else "tr"
        applied = False
        reply, _needs_key = self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Write one speech balloon for this manga panel. "
                        'Reply with JSON only: {"dialogue": "...", "balloon": "round|shout|thought|caption|none", "anchor": "tl|tr|bl|br"}. '
                        "No letters belong in the drawing."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Action: {panel.get('action') or ''}. Existing line: {dialogue}.",
                },
            ]
        )
        parsed = _json_object(reply or "")
        if parsed and str(parsed.get("dialogue") or "").strip():
            dialogue = _clip(parsed.get("dialogue"), 2000).strip()
            if parsed.get("balloon") in BALLOON_IDS:
                balloon = parsed["balloon"]
            if parsed.get("anchor") in ANCHOR_IDS:
                anchor = parsed["anchor"]
            applied = True
        if not applied:
            action = (panel.get("action") or "").strip()
            if action and not (panel.get("dialogue") or "").strip():
                dialogue = action.split(".")[0].split(";")[0].strip()[:2000]
                balloon = "round"
            elif not action and not (panel.get("dialogue") or "").strip():
                raise ValueError("Write an action or a line, and Koma can letter the balloon.")
        self.store.update_panel(
            panel["id"],
            {"dialogue": dialogue, "balloon": balloon, "balloon_anchor": anchor},
            {"dialogue", "balloon", "balloon_anchor"},
        )
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def panel_sketch(self, params: dict) -> dict:
        panel, _page, _chapter, project, _characters, _location, _slot = self._panel_bundle(params["id"])
        raw = str(params.get("png") or "").strip()
        if "," in raw and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            data = base64.b64decode(raw, validate=False)
        except Exception:
            raise ValueError("That sketch could not be read.") from None
        if not data or len(data) > 8_000_000:
            raise ValueError("That sketch is too large.")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if image.format != "PNG":
                    raise ValueError("The sketch needs to be a PNG.")
        except ValueError:
            raise
        except Exception:
            raise ValueError("That sketch could not be read.") from None
        path = str(save_image(data))
        saved = ""
        try:
            self.status("Refining the sketch…")
            built, _project = self._panel_build(panel["id"], sketch=path)
            saved = self._imagine(built["prompt"], built["aspect"], built["refs"])
            self.store.add_version(panel["id"], saved, built["prompt"], "Sketch")
        finally:
            if path and path != saved:
                unlink(path)
        self.store.touch(project["id"])
        return self._pack(project["id"])

    def panel_delete_ref(self, params: dict) -> dict:
        ref = self.store.get_ref(params["refId"])
        if ref["owner_kind"] == "panel":
            panel = self.store.get_panel(ref["owner_id"])
            page = self.store.get_page(panel["page_id"])
        elif ref["owner_kind"] == "page":
            page = self.store.get_page(ref["owner_id"])
        else:
            raise ValueError("That reference is not on a page or a panel.")
        chapter = self.store.get_chapter(page["chapter_id"])
        self.store.delete_ref(ref["id"])
        self.store.touch(chapter["project_id"])
        return self._pack(chapter["project_id"])

    def volume_create(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.get_project(project_id)
        title = _clip(params.get("title"), 160).strip() or f"Volume {len(self.store.list_volumes(project_id)) + 1}"
        self.store.create_volume(project_id, title, _clip(params.get("synopsis"), 4000))
        return self._pack(project_id)

    def volume_update(self, params: dict) -> dict:
        volume = self.store.get_volume(params["id"])
        fields = {}
        if "title" in params:
            title = _clip(params.get("title"), 160).strip()
            if title:
                fields["title"] = title
        if "synopsis" in params:
            fields["synopsis"] = _clip(params.get("synopsis"), 4000)
        self.store.update_volume(volume["id"], fields)
        self.store.touch(volume["project_id"])
        return self._pack(volume["project_id"])

    def volume_delete(self, params: dict) -> dict:
        volume = self.store.get_volume(params["id"])
        volumes = self.store.list_volumes(volume["project_id"])
        if len(volumes) <= 1:
            raise ValueError("Keep at least one volume.")
        replacement = str(params.get("replacementId") or "")
        if not replacement:
            replacement = next(item["id"] for item in volumes if item["id"] != volume["id"])
        other = self.store.get_volume(replacement)
        if other["project_id"] != volume["project_id"] or other["id"] == volume["id"]:
            raise ValueError("Choose another volume for these chapters.")
        self.store.delete_volume(volume["id"], other["id"])
        self.store.touch(volume["project_id"])
        return self._pack(volume["project_id"])

    def note_create(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.get_project(project_id)
        scope = params.get("scope") if params.get("scope") in {"story", "volume", "chapter", "page"} else "story"
        note = self.store.create_note(
            project_id,
            _clip(params.get("title"), 160).strip() or "Note",
            _clip(params.get("body"), 8000),
            scope,
            _clip(params.get("scopeId"), 80),
        )
        return self._pack(project_id, noteId=note["id"])

    def note_update(self, params: dict) -> dict:
        note = self.store.get_note(params["id"])
        fields = {}
        if "title" in params:
            title = _clip(params.get("title"), 160).strip()
            if title:
                fields["title"] = title
        if "body" in params:
            fields["body"] = _clip(params.get("body"), 8000)
        if "scope" in params and params["scope"] in {"story", "volume", "chapter", "page"}:
            fields["scope"] = params["scope"]
        if "scopeId" in params:
            fields["scope_id"] = _clip(params.get("scopeId"), 80)
        self.store.update_note(note["id"], fields)
        self.store.touch(note["project_id"])
        return self._pack(note["project_id"])

    def note_delete(self, params: dict) -> dict:
        note = self.store.get_note(params["id"])
        self.store.delete_note(note["id"])
        self.store.touch(note["project_id"])
        return self._pack(note["project_id"])

    def bot_create(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.get_project(project_id)
        bot = self.store.create_bot(
            project_id,
            _clip(params.get("name"), 80).strip() or "Grok",
            _clip(params.get("persona"), 2000).strip(),
        )
        return self._pack(project_id, botId=bot["id"])

    def bot_update(self, params: dict) -> dict:
        bot = self.store.get_bot(params["id"])
        fields = {}
        if "name" in params:
            name = _clip(params.get("name"), 80).strip()
            if name:
                fields["name"] = name
        if "persona" in params:
            fields["persona"] = _clip(params.get("persona"), 2000)
        self.store.update_bot(bot["id"], fields)
        self.store.touch(bot["project_id"])
        return self._pack(bot["project_id"])

    def bot_delete(self, params: dict) -> dict:
        bot = self.store.get_bot(params["id"])
        self.store.delete_bot(bot["id"])
        self.store.touch(bot["project_id"])
        return self._pack(bot["project_id"])

    def group_create(self, params: dict) -> dict:
        project_id = params["projectId"]
        self.store.get_project(project_id)
        bot_ids = self._project_bot_ids(project_id, params.get("botIds"))
        group = self.store.create_group(
            project_id,
            _clip(params.get("title"), 80).strip() or "Group",
            bot_ids,
        )
        return self._pack(project_id, groupId=group["id"])

    def group_update(self, params: dict) -> dict:
        group = self.store.get_group(params["id"])
        if "title" in params:
            title = _clip(params.get("title"), 80).strip()
            if title:
                self.store.update_group(group["id"], title)
        if "botIds" in params:
            self.store.set_group_members(group["id"], self._project_bot_ids(group["project_id"], params.get("botIds")))
        self.store.touch(group["project_id"])
        return self._pack(group["project_id"])

    def group_delete(self, params: dict) -> dict:
        group = self.store.get_group(params["id"])
        self.store.delete_group(group["id"])
        self.store.touch(group["project_id"])
        return self._pack(group["project_id"])

    def bot_history(self, params: dict) -> dict:
        group = self.store.get_group(params["id"])
        return {"messages": self._bot_messages(group)}

    def bot_send(self, params: dict) -> dict:
        group = self.store.get_group(params.get("groupId") or params.get("id"))
        project = self.store.get_project(group["project_id"])
        text = _clip(params.get("message"), 8000).strip()
        if not text:
            raise ValueError("Write a note for the group.")
        bots = []
        for bot_id in self.store.group_bot_ids(group["id"]):
            try:
                bot = self.store.get_bot(bot_id)
            except KeyError:
                continue
            if bot["project_id"] == project["id"]:
                bots.append(bot)
        if not bots:
            raise ValueError("Add a bot to this group.")
        self.store.add_bot_message(group["id"], "user", text)
        brief = self._studio_brief(project, params)[:8000]
        names = {bot["id"]: bot["name"] for bot in self.store.list_bots(project["id"])}
        for bot in bots:
            transcript = self.store.list_bot_messages(group["id"])[-30:]
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"You are {bot['name']}, a collaborator inside Koma. {bot.get('persona') or ''}\n\n"
                        f"What is open in Koma:\n{brief}"
                    ),
                }
            ]
            for item in transcript:
                if item["author"] == "user":
                    messages.append({"role": "user", "content": item["content"]})
                else:
                    speaker = names.get(item["author"], "Retired bot")
                    messages.append({"role": "assistant", "content": f"{speaker}: {item['content']}"})
            self.status(f"{bot['name']} is thinking…")
            reply = self.client.complete(messages)
            self.store.add_bot_message(group["id"], bot["id"], _clip(reply, 8000).strip() or "…")
        self.store.touch(project["id"])
        packed = self._pack(project["id"])
        packed["messages"] = self._bot_messages(group)
        return packed

    def _project_bot_ids(self, project_id: str, raw) -> list[str]:
        if isinstance(raw, str):
            raw = _loads(raw, [])
        if not isinstance(raw, list):
            return []
        known = {row["id"] for row in self.store.list_bots(project_id)}
        chosen = []
        for item in raw:
            if item in known and item not in chosen:
                chosen.append(item)
        return chosen

    def _bot_messages(self, group: dict) -> list[dict]:
        names = {bot["id"]: bot["name"] for bot in self.store.list_bots(group["project_id"])}
        rows = []
        for item in self.store.list_bot_messages(group["id"]):
            author = item["author"]
            rows.append(
                {
                    "id": item["id"],
                    "author": author,
                    "name": "You" if author == "user" else names.get(author, "Retired bot"),
                    "content": item["content"],
                    "createdAt": item["created_at"],
                }
            )
        return rows

    def _studio_brief(self, project: dict, focus: dict | None = None) -> str:
        focus = focus or {}
        project_id = project["id"]
        lines = [
            f"Title: {project.get('title') or 'Untitled'}",
            f"Premise: {project.get('premise') or ''}",
            f"Reading: {project.get('reading') or 'rtl'}",
            f"Open view: {focus.get('view') or ''}",
        ]
        lines.append("Volumes:")
        volumes = self.store.list_volumes(project_id)
        if not volumes:
            lines.append("- (none)")
        for volume in volumes:
            lines.append(f"- {volume['title']}: {volume.get('synopsis') or ''}")
        lines.append("Chapters:")
        chapters = self.store.list_chapters(project_id)
        if not chapters:
            lines.append("- (none)")
        page_id = str(focus.get("pageId") or "")
        panel_id = str(focus.get("panelId") or "")
        for chapter in chapters:
            lines.append(f"- {chapter['title']}: {chapter.get('synopsis') or ''}")
            if page_id:
                for page in self.store.list_pages(chapter["id"]):
                    if page["id"] != page_id:
                        continue
                    lines.append(f"Current page: {chapter['title']}, page {page['idx'] + 1}, layout {page['layout']}.")
                    if panel_id:
                        for panel in self.store.list_panels(page["id"]):
                            if panel["id"] == panel_id:
                                lines.append(
                                    f"Selected panel {panel['slot'] + 1}: {panel.get('action') or 'no action yet'}. "
                                    f"Dialogue: {panel.get('dialogue') or ''}."
                                )
        lines.append("Cast:")
        people = self.store.list_characters(project_id)
        lines.append(", ".join(person["name"] for person in people) or "(none)")
        lines.append("Places:")
        places = self.store.list_locations(project_id)
        lines.append(", ".join(place["name"] for place in places) or "(none)")
        return "\n".join(lines)

    def _complete(self, messages: list[dict]) -> tuple[str | None, bool]:
        try:
            return self.client.complete(messages), False
        except AIError as error:
            if "API key" in str(error):
                return None, True
            raise

    def reveal(self, params: dict) -> dict:
        target = Path(params.get("path") or "").expanduser().resolve()
        roots = [export_root().resolve(), data_dir().resolve()]
        if not any(target == root or root in target.parents for root in roots):
            raise ValueError("That folder is outside Koma.")
        folder = target if target.is_dir() else target.parent
        subprocess.Popen(["xdg-open", str(folder)])
        return {"ok": True}


def _page_meta(project: dict) -> dict:
    width, height = project_pixels(project)
    try:
        dpi = int(project.get("page_dpi") or 0)
        trim_w = float(project.get("trim_w_mm") or 0)
        trim_h = float(project.get("trim_h_mm") or 0)
    except (TypeError, ValueError):
        dpi, trim_w, trim_h = 0, 0.0, 0.0
    return {
        "pageKind": project.get("page_kind") or "",
        "pagePreset": project.get("page_preset") or "",
        "pageQuality": project.get("page_quality") or "",
        "pageUnit": project.get("page_unit") or "mm",
        "trimW": trim_w,
        "trimH": trim_h,
        "pageDpi": dpi,
        "pageWidth": width,
        "pageHeight": height,
    }


def _slot_count(layout: str) -> int:
    if layout not in LAYOUTS:
        return 1
    return len(LAYOUTS[layout][1])


def _page_slots_ltr(page: dict) -> list[dict] | None:
    raw = str(page.get("slots_json") or "").strip()
    if not raw:
        return None
    slots = _loads(raw, None)
    if not isinstance(slots, list) or not slots:
        return None
    cleaned = []
    for slot in slots:
        if not isinstance(slot, dict):
            return None
        try:
            cleaned.append({axis: float(slot[axis]) for axis in ("x", "y", "w", "h")})
        except (KeyError, TypeError, ValueError):
            return None
    return cleaned


def _page_count(page: dict) -> int:
    slots = _page_slots_ltr(page)
    if slots:
        return len(slots)
    return _slot_count(page.get("layout") or "")


def _objects_out(raw) -> list[dict]:
    return _clean_objects(_loads(raw, []) if not isinstance(raw, list) else raw)


def _clean_objects(raw) -> list[dict]:
    if isinstance(raw, str):
        raw = _loads(raw, [])
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            name = " ".join(item.split())[:80]
            on = True
        elif isinstance(item, dict):
            name = " ".join(str(item.get("name") or "").split())[:80]
            on = bool(item.get("on", True))
        else:
            continue
        if not name:
            continue
        if any(existing["name"].casefold() == name.casefold() for existing in out):
            continue
        out.append({"name": name, "on": on})
        if len(out) >= 12:
            break
    return out


def _guess_things(action: str) -> list[dict]:
    text = str(action or "").replace(";", ",").replace(" and ", ",")
    found = []
    for bit in text.split(","):
        name = " ".join(bit.split())[:80]
        if len(name) < 2:
            continue
        found.append({"name": name, "on": True})
        if len(found) >= 4:
            break
    return found


def _suggest_layout(beats: list, actions: list) -> str:
    count = len(actions) or len(beats)
    return {
        0: "classic",
        1: "full",
        2: "v2",
        3: "classic",
        4: "grid4",
        5: "top-heavy",
        6: "grid6",
    }.get(count, "v3")


def _json_object(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _png_bytes(raw: object) -> bytes:
    text = str(raw or "").strip()
    if "," in text and text.startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        data = base64.b64decode(text, validate=False)
    except Exception:
        raise ValueError("That picture could not be read.") from None
    if not data or len(data) > 8_000_000:
        raise ValueError("That picture is too large.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format != "PNG":
                raise ValueError("That picture needs to be a PNG.")
    except ValueError:
        raise
    except Exception:
        raise ValueError("That picture could not be read.") from None
    return data


def _thumb_png(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGBA")
        image.thumbnail((96, 96), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _direction(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return -1 if number < 0 else 1


def _bounded(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _project_prompt(project: dict) -> dict:
    return {
        "art_style": project.get("art_style") or "",
        "style_notes": project.get("style_notes") or "",
    }


ROUTES = {
    "bootstrap": Studio.bootstrap,
    "settings.save": Studio.settings_save,
    "project.create": Studio.project_create,
    "project.open": Studio.project_open,
    "project.update": Studio.project_update,
    "project.delete": Studio.project_delete,
    "story.send": Studio.story_send,
    "story.apply": Studio.story_apply,
    "story.discard": Studio.story_discard,
    "chapter.create": Studio.chapter_create,
    "chapter.update": Studio.chapter_update,
    "chapter.delete": Studio.chapter_delete,
    "chapter.duplicate": Studio.chapter_duplicate,
    "chapter.move": Studio.chapter_move,
    "library.search": Studio.library_search,
    "page.create": Studio.page_create,
    "page.update": Studio.page_update,
    "page.delete": Studio.page_delete,
    "page.undo": Studio.page_undo,
    "page.redo": Studio.page_redo,
    "page.move": Studio.page_move,
    "page.reorder": Studio.page_reorder,
    "page.duplicate": Studio.page_duplicate,
    "page.divide": Studio.page_divide,
    "page.suggest": Studio.page_suggest,
    "panel.update": Studio.panel_update,
    "panel.delete": Studio.panel_delete,
    "panel.move": Studio.panel_move,
    "panel.reorder": Studio.panel_reorder,
    "panel.preview": Studio.panel_preview,
    "panel.draw": Studio.panel_draw,
    "panel.draw_empty": Studio.panel_draw_empty,
    "panel.revise": Studio.panel_revise,
    "panel.use_version": Studio.panel_use_version,
    "panel.separate": Studio.panel_separate,
    "panel.letter": Studio.panel_letter,
    "panel.sketch": Studio.panel_sketch,
    "panel.delete_ref": Studio.panel_delete_ref,
    "volume.create": Studio.volume_create,
    "volume.update": Studio.volume_update,
    "volume.delete": Studio.volume_delete,
    "note.create": Studio.note_create,
    "note.update": Studio.note_update,
    "note.delete": Studio.note_delete,
    "bot.create": Studio.bot_create,
    "bot.update": Studio.bot_update,
    "bot.delete": Studio.bot_delete,
    "group.create": Studio.group_create,
    "group.update": Studio.group_update,
    "group.delete": Studio.group_delete,
    "bot.history": Studio.bot_history,
    "bot.send": Studio.bot_send,
    "character.create": Studio.character_create,
    "character.update": Studio.character_update,
    "character.delete": Studio.character_delete,
    "character.generate": Studio.character_generate,
    "character.revise": Studio.character_revise,
    "character.primary": Studio.character_primary,
    "character.delete_ref": Studio.character_delete_ref,
    "character.mesh": Studio.character_mesh,
    "character.mesh_import": Studio.character_mesh_import,
    "character.mesh_delete": Studio.character_mesh_delete,
    "character.mesh_export": Studio.character_mesh_export,
    "character.mesh_bytes": Studio.character_mesh_bytes,
    "character.mesh_thumb": Studio.character_mesh_thumb,
    "character.mesh_shot": Studio.character_mesh_shot,
    "location.create": Studio.location_create,
    "location.update": Studio.location_update,
    "location.delete": Studio.location_delete,
    "location.generate": Studio.location_generate,
    "ref.import": Studio.import_ref,
    "page.sheet": Studio.page_sheet,
    "chapter.sheets": Studio.chapter_sheets,
    "export.page": Studio.export_page,
    "export.chapter": Studio.export_chapter,
    "canvas.gallery": Studio.canvas_gallery,
    "canvas.prepare": Studio.canvas_prepare,
    "canvas.open_manga": Studio.canvas_open_manga,
    "canvas.open_illust": Studio.canvas_open_illust,
    "illust.update": Studio.illust_update,
    "illust.draw": Studio.illust_draw,
    "illust.revise": Studio.illust_revise,
    "illust.use_version": Studio.illust_use_version,
    "illust.delete": Studio.illust_delete,
    "illust.export": Studio.illust_export,
    "shell.reveal": Studio.reveal,
}
