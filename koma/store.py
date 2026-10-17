"""SQLite project store. One file under ~/.local/share/koma."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from koma.media import unlink


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return uuid.uuid4().hex


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  premise TEXT NOT NULL DEFAULT '',
  logline TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  tone TEXT NOT NULL DEFAULT '',
  art_style TEXT NOT NULL DEFAULT '',
  style_notes TEXT NOT NULL DEFAULT '',
  reading TEXT NOT NULL DEFAULT 'rtl',
  page_kind TEXT NOT NULL DEFAULT 'manga',
  page_preset TEXT NOT NULL DEFAULT '',
  trim_w_mm REAL NOT NULL DEFAULT 0,
  trim_h_mm REAL NOT NULL DEFAULT 0,
  page_dpi INTEGER NOT NULL DEFAULT 0,
  page_quality TEXT NOT NULL DEFAULT '',
  page_unit TEXT NOT NULL DEFAULT 'mm',
  pending_plan TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '',
  personality TEXT NOT NULL DEFAULT '',
  appearance TEXT NOT NULL DEFAULT '',
  wardrobe TEXT NOT NULL DEFAULT '',
  primary_ref_id TEXT,
  sort_idx INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  primary_ref_id TEXT,
  sort_idx INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refs (
  id TEXT PRIMARY KEY,
  owner_kind TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  image_path TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  title TEXT NOT NULL,
  synopsis TEXT NOT NULL DEFAULT '',
  beats TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
  id TEXT PRIMARY KEY,
  chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  layout TEXT NOT NULL DEFAULT 'classic',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS panels (
  id TEXT PRIMARY KEY,
  page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  slot INTEGER NOT NULL,
  shot TEXT NOT NULL DEFAULT 'medium',
  angle TEXT NOT NULL DEFAULT 'eye level',
  expression TEXT NOT NULL DEFAULT 'neutral',
  composition TEXT NOT NULL DEFAULT 'rule of thirds',
  action TEXT NOT NULL DEFAULT '',
  dialogue TEXT NOT NULL DEFAULT '',
  balloon TEXT NOT NULL DEFAULT 'round',
  balloon_anchor TEXT NOT NULL DEFAULT 'tr',
  sfx TEXT NOT NULL DEFAULT '',
  extra TEXT NOT NULL DEFAULT '',
  character_ids TEXT NOT NULL DEFAULT '[]',
  location_id TEXT,
  image_path TEXT,
  current_version_id TEXT,
  continue_prev INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
  id TEXT PRIMARY KEY,
  panel_id TEXT NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
  image_path TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS illustrations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  aspect TEXT NOT NULL DEFAULT '2:3',
  art_style TEXT NOT NULL DEFAULT '',
  image_path TEXT,
  current_version_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS illust_versions (
  id TEXT PRIMARY KEY,
  illust_id TEXT NOT NULL REFERENCES illustrations(id) ON DELETE CASCADE,
  image_path TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
"""


class StoreError(RuntimeError):
    """The library file cannot be opened."""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(str(path), timeout=0.5, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self._lock = threading.RLock()
            self._tx = 0
            self._pending: list[str] = []
            with self._lock:
                self.conn.executescript(SCHEMA)
                self._migrate()
                self.conn.commit()
        except sqlite3.DatabaseError as error:
            raise StoreError(
                "Koma's library file is damaged. Move koma.sqlite aside and start again."
            ) from error

    def _migrate(self) -> None:
        self._add_column("panels", "continue_prev", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("projects", "page_kind", "TEXT NOT NULL DEFAULT 'manga'")
        self._add_column("projects", "page_preset", "TEXT NOT NULL DEFAULT ''")
        self._add_column("projects", "trim_w_mm", "REAL NOT NULL DEFAULT 0")
        self._add_column("projects", "trim_h_mm", "REAL NOT NULL DEFAULT 0")
        self._add_column("projects", "page_dpi", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("projects", "page_quality", "TEXT NOT NULL DEFAULT ''")
        self._add_column("projects", "page_unit", "TEXT NOT NULL DEFAULT 'mm'")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS volumes (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              idx INTEGER NOT NULL,
              title TEXT NOT NULL,
              synopsis TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              idx INTEGER NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              scope TEXT NOT NULL DEFAULT 'story',
              scope_id TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bots (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              persona TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_groups (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              title TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_members (
              group_id TEXT NOT NULL,
              bot_id TEXT NOT NULL,
              PRIMARY KEY (group_id, bot_id)
            );
            CREATE TABLE IF NOT EXISTS bot_messages (
              id TEXT PRIMARY KEY,
              group_id TEXT NOT NULL,
              author TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meshes (
              id TEXT PRIMARY KEY,
              character_id TEXT NOT NULL,
              prompt TEXT NOT NULL DEFAULT '',
              provider TEXT NOT NULL,
              source TEXT NOT NULL,
              glb_path TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS poses (
              id TEXT PRIMARY KEY,
              character_id TEXT NOT NULL,
              name TEXT NOT NULL,
              bones_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        self._add_column("chapters", "volume_id", "TEXT NOT NULL DEFAULT ''")
        self._add_column("pages", "slots_json", "TEXT NOT NULL DEFAULT ''")
        self._add_column("pages", "prompt", "TEXT NOT NULL DEFAULT ''")
        self._add_column("pages", "character_ids", "TEXT NOT NULL DEFAULT '[]'")
        self._add_column("pages", "location_id", "TEXT")
        self._add_column("panels", "prompt", "TEXT NOT NULL DEFAULT ''")
        self._add_column("panels", "objects", "TEXT NOT NULL DEFAULT '[]'")
        self._add_column("panels", "vibe", "TEXT NOT NULL DEFAULT ''")
        self._add_column("meshes", "thumb_path", "TEXT NOT NULL DEFAULT ''")
        self._backfill_volumes()

    def _backfill_volumes(self) -> None:
        projects = self.conn.execute("SELECT id FROM projects").fetchall()
        for project in projects:
            project_id = project["id"]
            loose = self.conn.execute(
                "SELECT id FROM chapters WHERE project_id = ? AND (volume_id IS NULL OR volume_id = '')",
                (project_id,),
            ).fetchall()
            if not loose:
                continue
            existing = self.conn.execute(
                "SELECT id FROM volumes WHERE project_id = ? ORDER BY idx LIMIT 1",
                (project_id,),
            ).fetchone()
            if existing:
                volume_id = existing["id"]
            else:
                volume_id = new_id()
                self.conn.execute(
                    """INSERT INTO volumes (id, project_id, idx, title, synopsis, created_at)
                       VALUES (?, ?, 0, 'Volume 1', '', ?)""",
                    (volume_id, project_id, now()),
                )
            for chapter in loose:
                self.conn.execute(
                    "UPDATE chapters SET volume_id = ? WHERE id = ?",
                    (volume_id, chapter["id"]),
                )

    def _add_column(self, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if columns and column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    @contextmanager
    def batch(self):
        with self._lock:
            self._tx += 1
            try:
                yield
            except Exception:
                self._tx = 0
                self._pending = []
                self.conn.rollback()
                raise
            else:
                self._tx -= 1
                if self._tx == 0:
                    self.conn.commit()
                    pending = self._pending
                    self._pending = []
                    for path in pending:
                        unlink(path)

    def _drop(self, path: str | None) -> None:
        """Delete a file now, or after the open transaction commits."""
        if not path:
            return
        with self._lock:
            if self._tx:
                self._pending.append(path)
            else:
                unlink(path)

    def _fail(self, error: sqlite3.Error) -> None:
        if isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower():
            raise ValueError(
                "The library is open in another Koma window. Close it and try again."
            ) from error
        if isinstance(error, sqlite3.OperationalError):
            raise ValueError("Koma could not save. The library may be damaged.") from error
        raise ValueError(
            "Koma's library file is damaged. Move koma.sqlite aside and start again."
        ) from error

    def _write(self, sql: str, args: tuple | list = ()) -> None:
        with self._lock:
            try:
                self.conn.execute(sql, args)
                if self._tx == 0:
                    self.conn.commit()
            except sqlite3.Error as error:
                self._fail(error)

    def _all(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self._lock:
            try:
                rows = self.conn.execute(sql, args).fetchall()
            except sqlite3.Error as error:
                self._fail(error)
            return [dict(row) for row in rows]

    def _one(self, sql: str, args: tuple | list = ()) -> dict | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    def _next_idx(self, table: str, column: str, parent: str) -> int:
        index_col = "sort_idx" if table in {"characters", "locations"} else "idx"
        row = self._one(
            f"SELECT COALESCE(MAX({index_col}), -1) + 1 AS n FROM {table} WHERE {column} = ?",
            (parent,),
        )
        return int(row["n"]) if row else 0

    # --- projects ---

    def create_project(self, title: str, premise: str, art_style: str) -> dict:
        project_id = new_id()
        stamp = now()
        self._write(
            """INSERT INTO projects
               (id, title, premise, art_style, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, title, premise, art_style, stamp, stamp),
        )
        return self.get_project(project_id)

    def list_projects(self) -> list[dict]:
        return self._all("SELECT * FROM projects ORDER BY updated_at DESC")

    def get_project(self, project_id: str) -> dict:
        row = self._one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if row is None:
            raise KeyError("Project not found")
        return row

    def update_project(self, project_id: str, fields: dict, allowed: set[str]) -> None:
        data = {key: fields[key] for key in fields if key in allowed}
        if not data:
            return
        data["updated_at"] = now()
        sets = ", ".join(f"{key} = ?" for key in data)
        self._write(f"UPDATE projects SET {sets} WHERE id = ?", [*data.values(), project_id])

    def touch(self, project_id: str) -> None:
        self._write("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), project_id))

    def set_page_format(self, project_id: str, fields: dict) -> None:
        allowed = {
            "title",
            "reading",
            "page_kind",
            "page_preset",
            "trim_w_mm",
            "trim_h_mm",
            "page_dpi",
            "page_quality",
            "page_unit",
        }
        self.update_project(project_id, fields, allowed)

    def delete_project(self, project_id: str) -> None:
        paths = self.project_image_paths(project_id)
        groups = [row["id"] for row in self.list_groups(project_id)]
        bots = [row["id"] for row in self.list_bots(project_id)]
        with self.batch():
            for group_id in groups:
                self._write("DELETE FROM bot_messages WHERE group_id = ?", (group_id,))
                self._write("DELETE FROM bot_members WHERE group_id = ?", (group_id,))
            for bot_id in bots:
                self._write("DELETE FROM bot_members WHERE bot_id = ?", (bot_id,))
            self._write("DELETE FROM bot_groups WHERE project_id = ?", (project_id,))
            self._write("DELETE FROM bots WHERE project_id = ?", (project_id,))
            self._write("DELETE FROM notes WHERE project_id = ?", (project_id,))
            self._write(
                "DELETE FROM meshes WHERE character_id IN (SELECT id FROM characters WHERE project_id = ?)",
                (project_id,),
            )
            self._write(
                "DELETE FROM poses WHERE character_id IN (SELECT id FROM characters WHERE project_id = ?)",
                (project_id,),
            )
            self._write("DELETE FROM volumes WHERE project_id = ?", (project_id,))
            self._write(
                """
                DELETE FROM refs WHERE owner_id IN (
                    SELECT id FROM characters WHERE project_id = ?
                    UNION
                    SELECT id FROM locations WHERE project_id = ?
                    UNION
                    SELECT pages.id FROM pages
                    JOIN chapters ON chapters.id = pages.chapter_id
                    WHERE chapters.project_id = ?
                    UNION
                    SELECT panels.id FROM panels
                    JOIN pages ON pages.id = panels.page_id
                    JOIN chapters ON chapters.id = pages.chapter_id
                    WHERE chapters.project_id = ?
                )
                """,
                (project_id, project_id, project_id, project_id),
            )
            self._write("DELETE FROM projects WHERE id = ?", (project_id,))
        for path in paths:
            self._drop(path)

    def project_image_paths(self, project_id: str) -> list[str]:
        rows = self._all(
            """
            SELECT image_path AS p FROM refs
              WHERE owner_id IN (SELECT id FROM characters WHERE project_id = ?)
                 OR owner_id IN (SELECT id FROM locations WHERE project_id = ?)
                 OR owner_id IN (
                      SELECT pages.id FROM pages
                      JOIN chapters ON chapters.id = pages.chapter_id
                      WHERE chapters.project_id = ?
                 )
                 OR owner_id IN (
                      SELECT panels.id FROM panels
                      JOIN pages ON pages.id = panels.page_id
                      JOIN chapters ON chapters.id = pages.chapter_id
                      WHERE chapters.project_id = ?
                 )
            UNION
            SELECT image_path FROM panels
              WHERE page_id IN (
                SELECT pages.id FROM pages
                JOIN chapters ON chapters.id = pages.chapter_id
                WHERE chapters.project_id = ?
              )
            UNION
            SELECT image_path FROM versions
              WHERE panel_id IN (
                SELECT panels.id FROM panels
                JOIN pages ON pages.id = panels.page_id
                JOIN chapters ON chapters.id = pages.chapter_id
                WHERE chapters.project_id = ?
              )
            UNION
            SELECT glb_path FROM meshes
              WHERE character_id IN (SELECT id FROM characters WHERE project_id = ?)
            UNION
            SELECT thumb_path FROM meshes
              WHERE character_id IN (SELECT id FROM characters WHERE project_id = ?)
            """,
            (project_id, project_id, project_id, project_id, project_id, project_id, project_id, project_id),
        )
        return [row["p"] for row in rows if row["p"]]

    # --- characters / locations / refs ---

    def create_character(self, project_id: str, name: str) -> dict:
        character_id = new_id()
        self._write(
            """INSERT INTO characters (id, project_id, name, sort_idx, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (character_id, project_id, name, self._next_idx("characters", "project_id", project_id), now()),
        )
        self.touch(project_id)
        return self.get_character(character_id)

    def list_characters(self, project_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM characters WHERE project_id = ? ORDER BY sort_idx, created_at",
            (project_id,),
        )

    def get_character(self, character_id: str) -> dict:
        row = self._one("SELECT * FROM characters WHERE id = ?", (character_id,))
        if row is None:
            raise KeyError("Character not found")
        return row

    def update_character(self, character_id: str, fields: dict, allowed: set[str]) -> None:
        self._update("characters", character_id, fields, allowed)

    def delete_character(self, character_id: str) -> None:
        row = self.get_character(character_id)
        paths = [ref["image_path"] for ref in self.list_refs("character", character_id)]
        for mesh in self.list_meshes(character_id):
            paths.append(mesh["glb_path"])
            if mesh.get("thumb_path"):
                paths.append(mesh["thumb_path"])
        self._write("DELETE FROM refs WHERE owner_kind = 'character' AND owner_id = ?", (character_id,))
        self._write("DELETE FROM meshes WHERE character_id = ?", (character_id,))
        self._write("DELETE FROM poses WHERE character_id = ?", (character_id,))
        self._write("DELETE FROM characters WHERE id = ?", (character_id,))
        self.touch(row["project_id"])
        for path in paths:
            self._drop(path)

    def create_mesh(self, character_id: str, prompt: str, provider: str, source: str, glb_path: str) -> dict:
        mesh_id = new_id()
        self._write(
            """INSERT INTO meshes (id, character_id, prompt, provider, source, glb_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mesh_id, character_id, prompt, provider, source, glb_path, now()),
        )
        return self.get_mesh(mesh_id)

    def list_meshes(self, character_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM meshes WHERE character_id = ? ORDER BY created_at DESC, rowid DESC",
            (character_id,),
        )

    def get_mesh(self, mesh_id: str) -> dict:
        row = self._one("SELECT * FROM meshes WHERE id = ?", (mesh_id,))
        if row is None:
            raise KeyError("Model not found")
        return row

    def delete_mesh(self, mesh_id: str) -> dict:
        row = self.get_mesh(mesh_id)
        self._write("DELETE FROM meshes WHERE id = ?", (mesh_id,))
        self._drop(row["glb_path"])
        self._drop(row.get("thumb_path"))
        return row

    def save_pose(self, character_id: str, name: str, bones_json: str) -> dict:
        existing = self._one(
            "SELECT * FROM poses WHERE character_id = ? AND name = ?",
            (character_id, name),
        )
        if existing:
            self._write(
                "UPDATE poses SET bones_json = ?, created_at = ? WHERE id = ?",
                (bones_json, now(), existing["id"]),
            )
            return self.get_pose(existing["id"])
        pose_id = new_id()
        self._write(
            """INSERT INTO poses (id, character_id, name, bones_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (pose_id, character_id, name, bones_json, now()),
        )
        return self.get_pose(pose_id)

    def list_poses(self, character_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM poses WHERE character_id = ? ORDER BY created_at, rowid",
            (character_id,),
        )

    def get_pose(self, pose_id: str) -> dict:
        row = self._one("SELECT * FROM poses WHERE id = ?", (pose_id,))
        if row is None:
            raise KeyError("Pose not found")
        return row

    def delete_pose(self, pose_id: str) -> None:
        self.get_pose(pose_id)
        self._write("DELETE FROM poses WHERE id = ?", (pose_id,))

    def set_mesh_thumb(self, mesh_id: str, thumb_path: str) -> None:
        row = self.get_mesh(mesh_id)
        self._write("UPDATE meshes SET thumb_path = ? WHERE id = ?", (thumb_path, mesh_id))
        previous = row.get("thumb_path") or ""
        if previous and previous != thumb_path:
            self._drop(previous)

    def create_location(self, project_id: str, name: str) -> dict:
        location_id = new_id()
        self._write(
            """INSERT INTO locations (id, project_id, name, sort_idx, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (location_id, project_id, name, self._next_idx("locations", "project_id", project_id), now()),
        )
        self.touch(project_id)
        return self.get_location(location_id)

    def list_locations(self, project_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM locations WHERE project_id = ? ORDER BY sort_idx, created_at",
            (project_id,),
        )

    def get_location(self, location_id: str) -> dict:
        row = self._one("SELECT * FROM locations WHERE id = ?", (location_id,))
        if row is None:
            raise KeyError("Location not found")
        return row

    def update_location(self, location_id: str, fields: dict, allowed: set[str]) -> None:
        self._update("locations", location_id, fields, allowed)

    def delete_location(self, location_id: str) -> None:
        row = self.get_location(location_id)
        paths = [ref["image_path"] for ref in self.list_refs("location", location_id)]
        self._write("UPDATE panels SET location_id = NULL WHERE location_id = ?", (location_id,))
        self._write("UPDATE pages SET location_id = NULL WHERE location_id = ?", (location_id,))
        self._write("DELETE FROM refs WHERE owner_kind = 'location' AND owner_id = ?", (location_id,))
        self._write("DELETE FROM locations WHERE id = ?", (location_id,))
        self.touch(row["project_id"])
        for path in paths:
            self._drop(path)

    def create_ref(self, owner_kind: str, owner_id: str, kind: str, label: str, image_path: str, prompt: str) -> dict:
        ref_id = new_id()
        self._write(
            """INSERT INTO refs (id, owner_kind, owner_id, kind, label, image_path, prompt, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ref_id, owner_kind, owner_id, kind, label, image_path, prompt, now()),
        )
        return self.get_ref(ref_id)

    def list_refs(self, owner_kind: str, owner_id: str) -> list[dict]:
        return self._all(
            """SELECT * FROM refs WHERE owner_kind = ? AND owner_id = ?
               ORDER BY created_at DESC""",
            (owner_kind, owner_id),
        )

    def get_ref(self, ref_id: str) -> dict:
        row = self._one("SELECT * FROM refs WHERE id = ?", (ref_id,))
        if row is None:
            raise KeyError("Reference not found")
        return row

    def delete_ref(self, ref_id: str) -> dict:
        row = self.get_ref(ref_id)
        self._write("DELETE FROM refs WHERE id = ?", (ref_id,))
        self._drop(row["image_path"])
        return row

    def drop_owner_refs(self, owner_kind: str, owner_id: str) -> list[str]:
        refs = self.list_refs(owner_kind, owner_id)
        if not refs:
            return []
        self._write(
            "DELETE FROM refs WHERE owner_kind = ? AND owner_id = ?",
            (owner_kind, owner_id),
        )
        return [ref["image_path"] for ref in refs if ref.get("image_path")]

    # --- chapters / pages / panels ---

    def create_chapter(
        self,
        project_id: str,
        title: str,
        synopsis: str = "",
        beats: str = "[]",
        volume_id: str | None = None,
    ) -> dict:
        chapter_id = new_id()
        volume = self.ensure_volume(project_id, volume_id)
        self._write(
            """INSERT INTO chapters (id, project_id, volume_id, idx, title, synopsis, beats, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chapter_id,
                project_id,
                volume["id"],
                self._next_idx("chapters", "project_id", project_id),
                title,
                synopsis,
                beats,
                now(),
            ),
        )
        self.touch(project_id)
        return self.get_chapter(chapter_id)

    def list_chapters(self, project_id: str) -> list[dict]:
        return self._all("SELECT * FROM chapters WHERE project_id = ? ORDER BY idx", (project_id,))

    def get_chapter(self, chapter_id: str) -> dict:
        row = self._one("SELECT * FROM chapters WHERE id = ?", (chapter_id,))
        if row is None:
            raise KeyError("Chapter not found")
        return row

    def update_chapter(self, chapter_id: str, fields: dict, allowed: set[str]) -> None:
        self._update("chapters", chapter_id, fields, allowed)

    def delete_chapter(self, chapter_id: str) -> None:
        chapter = self.get_chapter(chapter_id)
        paths = self._chapter_images(chapter_id)
        for page in self.list_pages(chapter_id):
            paths.extend(self.drop_owner_refs("page", page["id"]))
            for panel in self.list_panels(page["id"]):
                paths.extend(self.drop_owner_refs("panel", panel["id"]))
        self._write("DELETE FROM chapters WHERE id = ?", (chapter_id,))
        self._reindex("chapters", "project_id", chapter["project_id"])
        self.touch(chapter["project_id"])
        for path in paths:
            self._drop(path)

    def create_page(self, chapter_id: str, layout: str, notes: str = "", slots_json: str = "") -> dict:
        page_id = new_id()
        self._write(
            """INSERT INTO pages (id, chapter_id, idx, layout, notes, slots_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (page_id, chapter_id, self._next_idx("pages", "chapter_id", chapter_id), layout, notes, slots_json or "", now()),
        )
        chapter = self.get_chapter(chapter_id)
        self.touch(chapter["project_id"])
        return self.get_page(page_id)

    def list_pages(self, chapter_id: str) -> list[dict]:
        return self._all("SELECT * FROM pages WHERE chapter_id = ? ORDER BY idx", (chapter_id,))

    def get_page(self, page_id: str) -> dict:
        row = self._one("SELECT * FROM pages WHERE id = ?", (page_id,))
        if row is None:
            raise KeyError("Page not found")
        return row

    def update_page(self, page_id: str, fields: dict, allowed: set[str]) -> None:
        self._update("pages", page_id, fields, allowed)

    def delete_page(self, page_id: str) -> None:
        page = self.get_page(page_id)
        paths = self._page_images(page_id)
        paths.extend(self.drop_owner_refs("page", page_id))
        for panel in self.list_panels(page_id):
            paths.extend(self.drop_owner_refs("panel", panel["id"]))
        self._write("DELETE FROM pages WHERE id = ?", (page_id,))
        self._reindex("pages", "chapter_id", page["chapter_id"])
        self.touch(self.get_chapter(page["chapter_id"])["project_id"])
        for path in paths:
            self._drop(path)

    def create_panel(self, page_id: str, slot: int, fields: dict | None = None) -> dict:
        panel_id = new_id()
        data = {
            "shot": "medium",
            "angle": "eye level",
            "expression": "neutral",
            "composition": "rule of thirds",
            "action": "",
            "dialogue": "",
            "balloon": "round",
            "balloon_anchor": "tr",
            "sfx": "",
            "extra": "",
            "character_ids": "[]",
            "location_id": None,
        }
        if fields:
            data.update(fields)
        self._write(
            """INSERT INTO panels (
                 id, page_id, slot, shot, angle, expression, composition, action,
                 dialogue, balloon, balloon_anchor, sfx, extra, character_ids,
                 location_id, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                panel_id,
                page_id,
                slot,
                data["shot"],
                data["angle"],
                data["expression"],
                data["composition"],
                data["action"],
                data["dialogue"],
                data["balloon"],
                data["balloon_anchor"],
                data["sfx"],
                data["extra"],
                data["character_ids"],
                data["location_id"],
                now(),
            ),
        )
        return self.get_panel(panel_id)

    def list_panels(self, page_id: str) -> list[dict]:
        return self._all("SELECT * FROM panels WHERE page_id = ? ORDER BY slot", (page_id,))

    def get_panel(self, panel_id: str) -> dict:
        row = self._one("SELECT * FROM panels WHERE id = ?", (panel_id,))
        if row is None:
            raise KeyError("Panel not found")
        return row

    def update_panel(self, panel_id: str, fields: dict, allowed: set[str]) -> None:
        self._update("panels", panel_id, fields, allowed)

    def delete_panel(self, panel_id: str) -> None:
        panel = self.get_panel(panel_id)
        with self.batch():
            paths = self.drop_owner_refs("panel", panel_id)
            for version in self.list_versions(panel_id):
                paths.append(version.get("image_path"))
            if panel.get("image_path"):
                paths.append(panel["image_path"])
            self._write("DELETE FROM panels WHERE id = ?", (panel_id,))
            later = self._all(
                "SELECT id, slot FROM panels WHERE page_id = ? AND slot > ? ORDER BY slot",
                (panel["page_id"], panel["slot"]),
            )
            for row in later:
                self._write("UPDATE panels SET slot = ? WHERE id = ?", (int(row["slot"]) - 1, row["id"]))
            for path in paths:
                self._drop(path)

    def wipe_panels(self, page_id: str) -> None:
        panels = self.list_panels(page_id)
        with self.batch():
            paths: list[str] = []
            for panel in panels:
                paths.extend(self.drop_owner_refs("panel", panel["id"]))
                for version in self.list_versions(panel["id"]):
                    if version.get("image_path"):
                        paths.append(version["image_path"])
                if panel.get("image_path"):
                    paths.append(panel["image_path"])
                self._write("DELETE FROM panels WHERE id = ?", (panel["id"],))
            for path in paths:
                self._drop(path)

    def insert_panel(self, page_id: str, fields: dict) -> dict:
        panel_id = fields["id"]
        self._write(
            """INSERT INTO panels (
                 id, page_id, slot, shot, angle, expression, composition, action,
                 dialogue, balloon, balloon_anchor, sfx, extra, character_ids,
                 location_id, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                panel_id,
                page_id,
                int(fields["slot"]),
                fields.get("shot") or "medium",
                fields.get("angle") or "eye level",
                fields.get("expression") or "neutral",
                fields.get("composition") or "rule of thirds",
                fields.get("action") or "",
                fields.get("dialogue") or "",
                fields.get("balloon") or "round",
                fields.get("balloon_anchor") or "tr",
                fields.get("sfx") or "",
                fields.get("extra") or "",
                fields.get("character_ids") or "[]",
                fields.get("location_id"),
                now(),
            ),
        )
        extra = {
            "continue_prev": int(fields.get("continue_prev") or 0),
            "prompt": fields.get("prompt") or "",
            "objects": fields.get("objects") or "[]",
            "vibe": fields.get("vibe") or "",
        }
        self.update_panel(panel_id, extra, set(extra))
        return self.get_panel(panel_id)

    def add_version(self, panel_id: str, image_path: str, prompt: str, note: str) -> dict:
        version_id = new_id()
        self._write(
            """INSERT INTO versions (id, panel_id, image_path, prompt, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (version_id, panel_id, image_path, prompt, note, now()),
        )
        self._write(
            "UPDATE panels SET image_path = ?, current_version_id = ? WHERE id = ?",
            (image_path, version_id, panel_id),
        )
        return self.get_version(version_id)

    def list_versions(self, panel_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM versions WHERE panel_id = ? ORDER BY created_at DESC",
            (panel_id,),
        )

    def get_version(self, version_id: str) -> dict:
        row = self._one("SELECT * FROM versions WHERE id = ?", (version_id,))
        if row is None:
            raise KeyError("Version not found")
        return row

    def use_version(self, panel_id: str, version_id: str) -> None:
        version = self.get_version(version_id)
        if version["panel_id"] != panel_id:
            raise KeyError("Version not found")
        self._write(
            "UPDATE panels SET image_path = ?, current_version_id = ? WHERE id = ?",
            (version["image_path"], version_id, panel_id),
        )

    def add_message(self, project_id: str, role: str, content: str) -> dict:
        message_id = new_id()
        self._write(
            "INSERT INTO messages (id, project_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (message_id, project_id, role, content, now()),
        )
        return self._one("SELECT * FROM messages WHERE id = ?", (message_id,))

    def list_messages(self, project_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM messages WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        )

    def latest_worked_page(self, project_id: str) -> dict | None:
        row = self._one(
            """
            SELECT pages.id AS id
            FROM versions
            JOIN panels ON panels.id = versions.panel_id
            JOIN pages ON pages.id = panels.page_id
            JOIN chapters ON chapters.id = pages.chapter_id
            WHERE chapters.project_id = ?
            ORDER BY versions.created_at DESC
            LIMIT 1
            """,
            (project_id,),
        )
        if row:
            return self.get_page(row["id"])
        for chapter in reversed(self.list_chapters(project_id)):
            pages = self.list_pages(chapter["id"])
            if pages:
                return pages[-1]
        return None

    def create_illustration(self, title: str, art_style: str, aspect: str = "2:3") -> dict:
        illust_id = new_id()
        stamp = now()
        self._write(
            """INSERT INTO illustrations
               (id, title, prompt, aspect, art_style, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?, ?)""",
            (illust_id, title, aspect, art_style, stamp, stamp),
        )
        return self.get_illustration(illust_id)

    def list_illustrations(self) -> list[dict]:
        return self._all("SELECT * FROM illustrations ORDER BY updated_at DESC")

    def get_illustration(self, illust_id: str) -> dict:
        row = self._one("SELECT * FROM illustrations WHERE id = ?", (illust_id,))
        if row is None:
            raise KeyError("Illustration not found")
        return row

    def update_illustration(self, illust_id: str, fields: dict) -> None:
        allowed = {"title", "prompt", "aspect", "art_style"}
        data = {key: fields[key] for key in fields if key in allowed}
        if not data:
            return
        data["updated_at"] = now()
        sets = ", ".join(f"{key} = ?" for key in data)
        self._write(f"UPDATE illustrations SET {sets} WHERE id = ?", [*data.values(), illust_id])

    def add_illust_version(self, illust_id: str, image_path: str, prompt: str, note: str) -> dict:
        version_id = new_id()
        stamp = now()
        self._write(
            """INSERT INTO illust_versions (id, illust_id, image_path, prompt, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (version_id, illust_id, image_path, prompt, note, stamp),
        )
        self._write(
            """UPDATE illustrations
               SET image_path = ?, current_version_id = ?, updated_at = ?
               WHERE id = ?""",
            (image_path, version_id, stamp, illust_id),
        )
        return self.get_illust_version(version_id)

    def list_illust_versions(self, illust_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM illust_versions WHERE illust_id = ? ORDER BY created_at DESC",
            (illust_id,),
        )

    def get_illust_version(self, version_id: str) -> dict:
        row = self._one("SELECT * FROM illust_versions WHERE id = ?", (version_id,))
        if row is None:
            raise KeyError("Version not found")
        return row

    def use_illust_version(self, illust_id: str, version_id: str) -> None:
        version = self.get_illust_version(version_id)
        if version["illust_id"] != illust_id:
            raise KeyError("Version not found")
        self._write(
            """UPDATE illustrations
               SET image_path = ?, current_version_id = ?, updated_at = ?
               WHERE id = ?""",
            (version["image_path"], version_id, now(), illust_id),
        )

    def delete_illustration(self, illust_id: str) -> None:
        row = self.get_illustration(illust_id)
        paths = [item["image_path"] for item in self.list_illust_versions(illust_id)]
        if row.get("image_path"):
            paths.append(row["image_path"])
        self._write("DELETE FROM illustrations WHERE id = ?", (illust_id,))
        for path in dict.fromkeys(paths):
            self._drop(path)

    def create_volume(self, project_id: str, title: str, synopsis: str = "") -> dict:
        volume_id = new_id()
        self._write(
            """INSERT INTO volumes (id, project_id, idx, title, synopsis, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (volume_id, project_id, self._next_idx("volumes", "project_id", project_id), title, synopsis, now()),
        )
        self.touch(project_id)
        return self.get_volume(volume_id)

    def list_volumes(self, project_id: str) -> list[dict]:
        return self._all("SELECT * FROM volumes WHERE project_id = ? ORDER BY idx", (project_id,))

    def get_volume(self, volume_id: str) -> dict:
        row = self._one("SELECT * FROM volumes WHERE id = ?", (volume_id,))
        if row is None:
            raise KeyError("Volume not found")
        return row

    def ensure_volume(self, project_id: str, volume_id: str | None = None) -> dict:
        if volume_id:
            volume = self.get_volume(volume_id)
            if volume["project_id"] != project_id:
                raise KeyError("Volume not found")
            return volume
        rows = self.list_volumes(project_id)
        if rows:
            return rows[0]
        return self.create_volume(project_id, "Volume 1")

    def update_volume(self, volume_id: str, fields: dict) -> None:
        data = {key: fields[key] for key in ("title", "synopsis") if key in fields}
        if not data:
            return
        sets = ", ".join(f"{key} = ?" for key in data)
        self._write(f"UPDATE volumes SET {sets} WHERE id = ?", [*data.values(), volume_id])

    def delete_volume(self, volume_id: str, replacement_id: str) -> None:
        self._write("UPDATE chapters SET volume_id = ? WHERE volume_id = ?", (replacement_id, volume_id))
        self._write("DELETE FROM volumes WHERE id = ?", (volume_id,))

    def set_page_slots(self, page_id: str, slots_json: str) -> None:
        self._write(
            "UPDATE pages SET layout = 'custom', slots_json = ? WHERE id = ?",
            (slots_json, page_id),
        )

    def clear_page_slots(self, page_id: str) -> None:
        self._write("UPDATE pages SET slots_json = '' WHERE id = ?", (page_id,))

    def shift_slots_after(self, page_id: str, index: int) -> None:
        rows = self._all(
            "SELECT id, slot FROM panels WHERE page_id = ? AND slot > ? ORDER BY slot DESC",
            (page_id, index),
        )
        for row in rows:
            self._write("UPDATE panels SET slot = ? WHERE id = ?", (int(row["slot"]) + 1, row["id"]))

    def create_note(self, project_id: str, title: str, body: str = "", scope: str = "story", scope_id: str = "") -> dict:
        note_id = new_id()
        stamp = now()
        self._write(
            """INSERT INTO notes (id, project_id, idx, title, body, scope, scope_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (note_id, project_id, self._next_idx("notes", "project_id", project_id), title, body, scope, scope_id, stamp, stamp),
        )
        self.touch(project_id)
        return self.get_note(note_id)

    def list_notes(self, project_id: str) -> list[dict]:
        return self._all("SELECT * FROM notes WHERE project_id = ? ORDER BY idx", (project_id,))

    def get_note(self, note_id: str) -> dict:
        row = self._one("SELECT * FROM notes WHERE id = ?", (note_id,))
        if row is None:
            raise KeyError("Note not found")
        return row

    def update_note(self, note_id: str, fields: dict) -> None:
        data = {key: fields[key] for key in ("title", "body", "scope", "scope_id") if key in fields}
        if not data:
            return
        data["updated_at"] = now()
        sets = ", ".join(f"{key} = ?" for key in data)
        self._write(f"UPDATE notes SET {sets} WHERE id = ?", [*data.values(), note_id])

    def delete_note(self, note_id: str) -> None:
        self._write("DELETE FROM notes WHERE id = ?", (note_id,))

    def create_bot(self, project_id: str, name: str, persona: str) -> dict:
        bot_id = new_id()
        self._write(
            "INSERT INTO bots (id, project_id, name, persona, created_at) VALUES (?, ?, ?, ?, ?)",
            (bot_id, project_id, name, persona, now()),
        )
        self.touch(project_id)
        return self.get_bot(bot_id)

    def list_bots(self, project_id: str) -> list[dict]:
        return self._all("SELECT * FROM bots WHERE project_id = ? ORDER BY created_at", (project_id,))

    def get_bot(self, bot_id: str) -> dict:
        row = self._one("SELECT * FROM bots WHERE id = ?", (bot_id,))
        if row is None:
            raise KeyError("Bot not found")
        return row

    def delete_bot(self, bot_id: str) -> None:
        self._write("DELETE FROM bot_members WHERE bot_id = ?", (bot_id,))
        self._write("DELETE FROM bots WHERE id = ?", (bot_id,))

    def update_bot(self, bot_id: str, fields: dict) -> None:
        data = {key: fields[key] for key in ("name", "persona") if key in fields}
        if not data:
            return
        sets = ", ".join(f"{key} = ?" for key in data)
        self._write(f"UPDATE bots SET {sets} WHERE id = ?", [*data.values(), bot_id])

    def update_group(self, group_id: str, title: str) -> None:
        self._write("UPDATE bot_groups SET title = ? WHERE id = ?", (title, group_id))

    def set_group_members(self, group_id: str, bot_ids: list[str]) -> None:
        self._write("DELETE FROM bot_members WHERE group_id = ?", (group_id,))
        for bot_id in bot_ids:
            self._write(
                "INSERT OR IGNORE INTO bot_members (group_id, bot_id) VALUES (?, ?)",
                (group_id, bot_id),
            )

    def create_group(self, project_id: str, title: str, bot_ids: list[str]) -> dict:
        group_id = new_id()
        self._write(
            "INSERT INTO bot_groups (id, project_id, title, created_at) VALUES (?, ?, ?, ?)",
            (group_id, project_id, title, now()),
        )
        for bot_id in bot_ids:
            self._write("INSERT OR IGNORE INTO bot_members (group_id, bot_id) VALUES (?, ?)", (group_id, bot_id))
        self.touch(project_id)
        return self.get_group(group_id)

    def list_groups(self, project_id: str) -> list[dict]:
        return self._all("SELECT * FROM bot_groups WHERE project_id = ? ORDER BY created_at", (project_id,))

    def get_group(self, group_id: str) -> dict:
        row = self._one("SELECT * FROM bot_groups WHERE id = ?", (group_id,))
        if row is None:
            raise KeyError("Group not found")
        return row

    def group_bot_ids(self, group_id: str) -> list[str]:
        rows = self._all("SELECT bot_id FROM bot_members WHERE group_id = ? ORDER BY bot_id", (group_id,))
        return [row["bot_id"] for row in rows]

    def delete_group(self, group_id: str) -> None:
        self._write("DELETE FROM bot_messages WHERE group_id = ?", (group_id,))
        self._write("DELETE FROM bot_members WHERE group_id = ?", (group_id,))
        self._write("DELETE FROM bot_groups WHERE id = ?", (group_id,))

    def add_bot_message(self, group_id: str, author: str, content: str) -> dict:
        message_id = new_id()
        self._write(
            "INSERT INTO bot_messages (id, group_id, author, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (message_id, group_id, author, content, now()),
        )
        return self._one("SELECT * FROM bot_messages WHERE id = ?", (message_id,)) or {}

    def list_bot_messages(self, group_id: str) -> list[dict]:
        return self._all("SELECT * FROM bot_messages WHERE group_id = ? ORDER BY created_at", (group_id,))

    def move(self, table: str, parent_col: str, item_id: str, direction: int) -> None:
        if table not in {"chapters", "pages"} or parent_col not in {"project_id", "chapter_id"}:
            raise ValueError("Cannot move that")
        row = self._one(f"SELECT * FROM {table} WHERE id = ?", (item_id,))
        if row is None:
            raise KeyError("Not found")
        parent = row[parent_col]
        items = self._all(f"SELECT id FROM {table} WHERE {parent_col} = ? ORDER BY idx", (parent,))
        ids = [item["id"] for item in items]
        index = ids.index(item_id)
        swap = index + direction
        if swap < 0 or swap >= len(ids):
            return
        ids[index], ids[swap] = ids[swap], ids[index]
        self.reorder(table, parent_col, parent, ids)

    def reorder(self, table: str, parent_col: str, parent_id: str, ids: list[str]) -> None:
        if table not in {"chapters", "pages"} or parent_col not in {"project_id", "chapter_id"}:
            raise ValueError("Cannot move that")
        current = [
            row["id"]
            for row in self._all(f"SELECT id FROM {table} WHERE {parent_col} = ? ORDER BY idx", (parent_id,))
        ]
        if len(ids) != len(set(ids)) or sorted(ids) != sorted(current):
            raise ValueError("That order does not match the pages in this chapter.")
        with self.batch():
            for idx, moved in enumerate(ids):
                self._write(f"UPDATE {table} SET idx = ? WHERE id = ?", (idx, moved))

    def _update(self, table: str, item_id: str, fields: dict, allowed: set[str]) -> None:
        if table not in {"characters", "locations", "chapters", "pages", "panels"}:
            raise ValueError("Bad table")
        data = {key: fields[key] for key in fields if key in allowed}
        if not data:
            return
        sets = ", ".join(f"{key} = ?" for key in data)
        self._write(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), item_id])

    def _reindex(self, table: str, parent_col: str, parent: str) -> None:
        items = self._all(f"SELECT id FROM {table} WHERE {parent_col} = ? ORDER BY idx", (parent,))
        with self.batch():
            for idx, item in enumerate(items):
                self._write(f"UPDATE {table} SET idx = ? WHERE id = ?", (idx, item["id"]))

    def _page_images(self, page_id: str) -> list[str]:
        rows = self._all(
            """
            SELECT image_path AS p FROM panels WHERE page_id = ?
            UNION
            SELECT image_path FROM versions
              WHERE panel_id IN (SELECT id FROM panels WHERE page_id = ?)
            """,
            (page_id, page_id),
        )
        return [row["p"] for row in rows if row["p"]]

    def _chapter_images(self, chapter_id: str) -> list[str]:
        rows = self._all(
            """
            SELECT image_path AS p FROM panels
              WHERE page_id IN (SELECT id FROM pages WHERE chapter_id = ?)
            UNION
            SELECT image_path FROM versions
              WHERE panel_id IN (
                SELECT panels.id FROM panels
                JOIN pages ON pages.id = panels.page_id
                WHERE pages.chapter_id = ?
              )
            """,
            (chapter_id, chapter_id),
        )
        return [row["p"] for row in rows if row["p"]]
