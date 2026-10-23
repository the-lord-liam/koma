"""Studio behavior that does not need the window or the network."""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from koma.ai import image_request
from koma.compose import render_page
from koma.layouts import LayoutError, aspect_for_slot, assist_divide, place
from koma.prompts import build_panel, extract_plan, illustration_prompt
from koma.service import Studio
from koma.store import Store


class FakeClient:
    def complete(self, messages, model=None):
        return (
            "Tightened the opening.\n\n```json\n"
            + json.dumps(
                {
                    "title": "Tide",
                    "logline": "A runner steals a minute.",
                    "art_style": "ink manga",
                    "cast": [
                        {
                            "name": "Aki",
                            "role": "runner",
                            "appearance": "short black hair and a scar on the chin",
                            "wardrobe": "red jacket",
                        }
                    ],
                    "locations": [{"name": "Pier", "description": "night pier, wet boards"}],
                    "chapters": [
                        {
                            "title": "Wake",
                            "synopsis": "Aki runs.",
                            "beats": ["run", "stop"],
                            "pages": [
                                {
                                    "layout": "not-a-layout",
                                    "panels": [
                                        {
                                            "shot": "wide",
                                            "action": "sprints",
                                            "dialogue": "Go",
                                            "characters": ["Aki"],
                                            "location": "Pier",
                                        }
                                    ],
                                },
                                {
                                    "layout": "v2",
                                    "panels": [
                                        {"action": "looks back", "characters": ["Aki"], "dialogue": "Wait"},
                                        {"shot": "close-up", "action": "stops"},
                                    ],
                                },
                            ],
                        }
                    ],
                }
            )
            + "\n```"
        )

    def imagine(self, prompt, aspect, refs, **kwargs):
        if not hasattr(self, "prompts"):
            self.prompts = []
        self.prompts.append((prompt, aspect, list(refs)))
        self.image_kwargs = dict(kwargs)
        image = Image.new("RGB", (32, 48), (180, 30, 30))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["KOMA_DATA_DIR"] = str(root / "data")
        os.environ["KOMA_CONFIG_DIR"] = str(root / "config")
        os.environ["KOMA_EXPORT_DIR"] = str(root / "export")
        self.store = Store(root / "studio.sqlite")
        self.studio = Studio(self.store, client=FakeClient())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_plan_apply_draw_and_export(self):
        created = self.studio.project_create({"title": "Draft", "premise": "A theft at the pier."})
        project_id = created["tree"]["project"]["id"]
        sent = self.studio.story_send({"projectId": project_id, "mode": "plan", "chapters": 1, "pagesPer": 2})
        self.assertTrue(sent["hasPlan"])
        self.assertIn("Tightened", sent["tree"]["messages"][-1]["content"])
        self.assertNotIn("```", sent["tree"]["messages"][-1]["content"])
        applied = self.studio.story_apply({"projectId": project_id, "mode": "replace"})
        tree = applied["tree"]
        self.assertEqual(tree["project"]["title"], "Tide")
        self.assertEqual(tree["project"]["pendingPlan"], None)
        self.assertEqual(tree["characters"][0]["name"], "Aki")
        self.assertEqual(tree["locations"][0]["name"], "Pier")
        self.assertEqual(len(tree["chapters"]), 1)
        pages = tree["chapters"][0]["pages"]
        self.assertEqual(pages[0]["layout"], "classic")
        self.assertEqual(len(pages[0]["panels"]), 3)
        self.assertEqual(pages[1]["layout"], "v2")
        opening = pages[0]["panels"][0]
        self.assertEqual(opening["locationId"], tree["locations"][0]["id"])
        self.assertEqual(opening["dialogue"], "Go")
        lead = pages[1]["panels"][0]
        self.assertIn(tree["characters"][0]["id"], lead["characterIds"])
        self.assertEqual(lead["dialogue"], "Wait")

        self.studio.character_generate({"id": tree["characters"][0]["id"], "kind": "portrait"})
        drawn = self.studio.panel_draw({"id": lead["id"]})
        panel = drawn["tree"]["chapters"][0]["pages"][1]["panels"][0]
        self.assertTrue(panel["imageUrl"].startswith("koma:///media/"))
        self.assertEqual(len(panel["versions"]), 1)

        preview = self.studio.panel_preview({"id": panel["id"]})
        self.assertIn("short black hair", preview["prompt"])
        self.assertIn("No written text", preview["prompt"])
        self.assertIn("<IMAGE_0>", preview["prompt"])
        self.assertGreaterEqual(preview["refCount"], 1)

        exported = self.studio.export_page({"pageId": pages[1]["id"]})
        self.assertTrue(Path(exported["path"]).exists())
        with Image.open(exported["path"]) as image:
            self.assertEqual(image.size, (1200, 1800))

        chapter_id = tree["chapters"][0]["id"]
        pdf = self.studio.export_chapter({"chapterId": chapter_id, "format": "pdf"})
        self.assertTrue(pdf["path"].endswith(".pdf"))
        self.assertGreater(Path(pdf["path"]).stat().st_size, 1000)

        self.studio.project_delete({"id": project_id})
        self.assertEqual(self.studio.bootstrap({})["projects"], [])

    def test_empty_panels_reader_and_import(self):
        created = self.studio.project_create({"title": "Batch", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        page = self.studio.page_create({"chapterId": chapter["id"], "layout": "v2"})["tree"]["chapters"][0]["pages"][0]
        self.assertEqual(len(page["panels"]), 2)
        self.studio.panel_draw({"id": page["panels"][0]["id"]})
        filled = self.studio.panel_draw_empty({"pageId": page["id"]})
        self.assertEqual(filled["drawn"], 1)
        self.assertEqual(filled["total"], 1)
        self.assertIsNone(filled["warning"])
        again = self.studio.panel_draw_empty({"pageId": page["id"]})
        self.assertEqual(again["drawn"], 0)
        self.assertIn("already has a drawing", again["warning"])

        sheets = self.studio.chapter_sheets({"chapterId": chapter["id"]})
        self.assertEqual(len(sheets["sheets"]), 1)
        preview = Path(os.environ["KOMA_DATA_DIR"]) / "previews" / f"{page['id']}.png"
        self.assertTrue(preview.exists())
        self.assertTrue(sheets["sheets"][0]["url"].endswith(f"{page['id']}.png"))

        person = self.studio.character_create({"projectId": project_id, "name": "Nori"})["tree"]["characters"][0]
        face = Path(self.tmp.name) / "face.png"
        Image.new("RGB", (16, 20), (20, 20, 20)).save(face)
        imported = self.studio.import_ref({"owner": "character", "id": person["id"], "path": str(face), "kind": "portrait"})
        saved = next(item for item in imported["tree"]["characters"] if item["id"] == person["id"])
        self.assertTrue(saved["primaryRefId"])
        self.assertEqual(saved["refs"][0]["kind"], "portrait")

        junk = Path(self.tmp.name) / "notes.txt"
        junk.write_text("not a picture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.studio.import_ref({"owner": "character", "id": person["id"], "path": str(junk)})
        with self.assertRaises(ValueError):
            self.studio.import_ref({"owner": "character", "id": person["id"], "path": "/etc/hostname"})

    def test_revise_needs_a_drawing(self):
        created = self.studio.project_create({"title": "Empty", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        page = self.studio.page_create({"chapterId": chapter["id"]})["tree"]["chapters"][0]["pages"][0]
        panel_id = page["panels"][0]["id"]
        with self.assertRaises(ValueError):
            self.studio.panel_revise({"id": panel_id, "instruction": "Closer."})

    def test_extract_plan_and_mirror(self):
        prose, plan = extract_plan("Hello\n```json\n{\"title\": \"Tide\"}\n```")
        self.assertEqual(prose, "Hello")
        self.assertEqual(plan["title"], "Tide")
        prose, plan = extract_plan("Just a question?")
        self.assertIsNone(plan)
        slots = [{"x": 0, "y": 0, "w": 0.5, "h": 1}, {"x": 0.5, "y": 0, "w": 0.5, "h": 1}]
        mirrored = place(slots, "rtl")
        self.assertAlmostEqual(mirrored[0]["x"], 0.5)
        self.assertEqual(aspect_for_slot({"w": 1, "h": 1}), "2:3")

    def test_prompt_uses_identity_and_forbids_lettering(self):
        built = build_panel(
            {"art_style": "ink"},
            {"shot": "close-up", "angle": "low angle", "expression": "angry", "composition": "diagonal", "action": "swings", "extra": ""},
            {"x": 0, "y": 0, "w": 1, "h": 1},
            [{"name": "Aki", "appearance": "short black hair", "wardrobe": "red jacket", "ref_path": "/tmp/aki.png"}],
            {"name": "Pier", "description": "wet boards", "ref_path": "/tmp/pier.png"},
        )
        self.assertIn("<IMAGE_0>", built["prompt"])
        self.assertIn("short black hair", built["prompt"])
        self.assertIn("No written text", built["prompt"])
        self.assertEqual(built["refs"], ["/tmp/aki.png", "/tmp/pier.png"])
        continued = build_panel(
            {"art_style": "ink"},
            {"shot": "medium", "angle": "eye level", "expression": "neutral", "composition": "rule of thirds", "action": "turns", "extra": ""},
            {"x": 0, "y": 0, "w": 1, "h": 1},
            [{"name": "Aki", "appearance": "short black hair", "wardrobe": "red jacket", "ref_path": "/tmp/aki.png"}],
            {"name": "Pier", "description": "wet boards", "ref_path": "/tmp/pier.png"},
            previous_image="/tmp/prev.png",
        )
        self.assertEqual(continued["refs"], ["/tmp/aki.png", "/tmp/prev.png", "/tmp/pier.png"])
        self.assertIn("previous panel", continued["prompt"])
        revised = build_panel(
            {"art_style": "ink"},
            {"action": "turns"},
            {"w": 1, "h": 1},
            [{"name": "Aki", "ref_path": "/tmp/aki.png"}],
            None,
            revise="Closer.",
            current_image="/tmp/now.png",
            previous_image="/tmp/prev.png",
        )
        self.assertEqual(revised["refs"][0], "/tmp/now.png")
        self.assertNotIn("/tmp/prev.png", revised["refs"])
        with self.assertRaises(ValueError):
            build_panel({"art_style": "ink"}, {}, {"w": 1, "h": 1}, [], None, revise="Closer.")

    def test_continue_previous_and_duplicate_page(self):
        created = self.studio.project_create({"title": "Relay", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        page = self.studio.page_create({"chapterId": chapter["id"], "layout": "v2"})["tree"]["chapters"][0]["pages"][0]
        first, second = page["panels"]
        self.studio.panel_update({"id": first["id"], "action": "runs", "dialogue": "Go"})
        self.studio.panel_draw({"id": first["id"]})
        self.studio.panel_update({"id": second["id"], "continuePrev": True, "action": "looks back"})
        preview = self.studio.panel_preview({"id": second["id"]})
        self.assertIn("previous panel", preview["prompt"])
        self.assertEqual(preview["refCount"], 1)
        self.assertEqual(preview["warnings"], [])
        opening = self.studio.panel_update({"id": first["id"], "continuePrev": True})
        flagged = next(panel for panel in opening["tree"]["chapters"][0]["pages"][0]["panels"] if panel["id"] == first["id"])
        self.assertTrue(flagged["continuePrev"])
        lonely = self.studio.panel_preview({"id": first["id"]})
        self.assertTrue(any("no earlier panel" in warning for warning in lonely["warnings"]))

        source_path = self.store.get_panel(first["id"])["image_path"]
        copied = self.studio.page_duplicate({"pageId": page["id"]})
        pages = copied["tree"]["chapters"][0]["pages"]
        self.assertEqual([item["id"] for item in pages], [page["id"], copied["pageId"]])
        twin = pages[1]["panels"][0]
        self.assertEqual(twin["dialogue"], "Go")
        self.assertEqual(twin["action"], "runs")
        self.assertTrue(twin["continuePrev"])
        copy_path = self.store.get_panel(twin["id"])["image_path"]
        self.assertNotEqual(copy_path, source_path)
        self.assertTrue(Path(copy_path).is_file())
        self.assertEqual(
            self.studio._previous_image(self.store.get_page(copied["pageId"]), self.store.get_panel(twin["id"])),
            source_path,
        )
        self.studio.page_delete({"id": page["id"]})
        self.assertFalse(Path(source_path).exists())
        self.assertTrue(Path(copy_path).is_file())

        follower = pages[1]["panels"][1]
        followed = self.studio.panel_preview({"id": follower["id"]})
        self.assertIn("previous panel", followed["prompt"])
        self.assertEqual(
            self.studio._previous_image(self.store.get_page(copied["pageId"]), self.store.get_panel(follower["id"])),
            copy_path,
        )

    def test_existing_database_gains_continue_flag(self):
        path = Path(self.tmp.name) / "legacy.sqlite"
        raw = sqlite3.connect(path)
        raw.execute(
            """CREATE TABLE panels (
                id TEXT PRIMARY KEY,
                page_id TEXT,
                slot INTEGER,
                created_at TEXT
            )"""
        )
        raw.execute("INSERT INTO panels (id, page_id, slot, created_at) VALUES ('p', 'page', 0, 'now')")
        raw.commit()
        raw.close()
        store = Store(path)
        try:
            row = store._one("SELECT continue_prev FROM panels WHERE id = 'p'")
            self.assertEqual(row["continue_prev"], 0)
        finally:
            store.close()

    def test_image_request_shape(self):
        picture = Path(self.tmp.name) / "ref.png"
        Image.new("RGB", (12, 12), "blue").save(picture)
        path, body = image_request("a panel", "2:3", [], model="grok-imagine-image-2.0", resolution="1k")
        self.assertEqual(path, "/images/generations")
        self.assertNotIn("images", body)
        self.assertNotIn("quality", body)
        path, body = image_request(
            "same person <IMAGE_0>",
            "2:3",
            [str(picture)],
            model="grok-imagine-image-2.0",
            resolution="1k",
        )
        self.assertEqual(path, "/images/edits")
        self.assertEqual(body["images"][0]["type"], "image_url")
        self.assertTrue(body["images"][0]["url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("quality", body)
        self.assertNotIn("xai-", json.dumps(body))
        _path, pinned = image_request(
            "a panel",
            "2:3",
            [],
            model="grok-imagine-image-2.0",
            resolution="2k",
            quality="medium",
        )
        self.assertEqual(pinned["quality"], "medium")
        self.assertEqual(pinned["resolution"], "2k")
        _path, skipped = image_request(
            "a panel",
            "2:3",
            [],
            model="grok-imagine-image-2.0",
            resolution="1k",
            quality="draft",
        )
        self.assertNotIn("quality", skipped)

    def test_page_lettering_and_reading_direction(self):
        red = Path(self.tmp.name) / "red.png"
        blue = Path(self.tmp.name) / "blue.png"
        Image.new("RGB", (40, 80), (220, 0, 0)).save(red)
        Image.new("RGB", (40, 80), (0, 0, 220)).save(blue)
        panels = [
            {"slot": 0, "image_path": str(red), "dialogue": "", "balloon": "none", "balloon_anchor": "tr", "sfx": ""},
            {"slot": 1, "image_path": str(blue), "dialogue": "", "balloon": "none", "balloon_anchor": "tr", "sfx": ""},
        ]
        ltr = Path(self.tmp.name) / "ltr.png"
        rtl = Path(self.tmp.name) / "rtl.png"
        render_page("h2", "ltr", panels, "1-1", ltr)
        render_page("h2", "rtl", panels, "1-1", rtl)
        left = Image.open(ltr)
        right = Image.open(rtl)
        self.assertEqual(left.size, (1200, 1800))
        self.assertGreater(left.getpixel((120, 900))[0], 150)
        self.assertGreater(right.getpixel((1080, 900))[0], 150)
        self.assertGreater(left.getpixel((1080, 900))[2], 150)
        spoken = Path(self.tmp.name) / "said.png"
        render_page(
            "full",
            "ltr",
            [{
                "slot": 0,
                "image_path": str(red),
                "dialogue": "走って",
                "balloon": "round",
                "balloon_anchor": "tr",
                "sfx": "ドン",
            }],
            "1-2",
            spoken,
        )
        self.assertNotEqual(Image.open(spoken).tobytes(), render_silent(red, spoken.parent / "quiet.png"))


    def test_two_canvases(self):
        blank = illustration_prompt({"prompt": "a red crane", "art_style": "watercolor"})
        self.assertIn("not a comic page", blank)
        self.assertIn("No written text", blank)
        self.assertNotIn("<IMAGE_0>", blank)

        opened = self.studio.canvas_open_manga({"create": True})
        project_id = opened["tree"]["project"]["id"]
        self.assertEqual(opened["tree"]["project"]["title"], "Untitled")
        self.assertEqual(len(opened["tree"]["chapters"][0]["pages"]), 1)
        self.assertEqual(len(opened["tree"]["chapters"][0]["pages"][0]["panels"]), 3)
        self.assertEqual(opened["mangaBoards"][0]["layout"], "classic")
        again = self.studio.canvas_open_manga({"projectId": project_id})
        self.assertEqual(len(again["tree"]["chapters"][0]["pages"]), 1)

        made = self.studio.canvas_open_illust({})
        self.assertEqual(len(made["illustrations"]), 1)
        illust_id = made["illustId"]
        drawn = self.studio.illust_draw({"id": illust_id, "prompt": "a red paper crane on a table"})
        art = drawn["illustrations"][0]
        self.assertTrue(art["imageUrl"].startswith("koma:///media/"))
        self.assertEqual(art["aspect"], "2:3")
        self.assertIn("No written text", self.studio.client.prompts[-1][0])
        self.assertEqual(self.studio.client.prompts[-1][1], "2:3")
        self.assertEqual(self.studio.client.prompts[-1][2], [])
        first_path = self.store.get_illustration(illust_id)["image_path"]

        revised = self.studio.illust_revise({"id": illust_id, "instruction": "Make the background dusk."})
        self.assertEqual(len(revised["illustrations"][0]["versions"]), 2)
        self.assertIn("<IMAGE_0>", self.studio.client.prompts[-1][0])
        self.assertEqual(len(self.studio.client.prompts[-1][2]), 1)
        same = self.studio.canvas_open_illust({})
        self.assertEqual(len(same["illustrations"]), 1)

        exported = self.studio.illust_export({"id": illust_id})
        self.assertTrue(Path(exported["path"]).is_file())
        self.studio.illust_delete({"id": illust_id})
        self.assertEqual(self.studio.canvas_gallery({})["illustrations"], [])
        self.assertFalse(Path(first_path).exists())

        second = self.studio.canvas_open_manga({"create": True})
        self.assertEqual(len(second["mangaBoards"]), 2)

    def test_book_setup_page_size(self):
        from koma.compose import PAGE_H, PAGE_W
        from koma.formats import page_pixels, project_pixels, resolve_format

        self.assertEqual(project_pixels({}), (PAGE_W, PAGE_H))
        b6 = page_pixels(128, 182, 300)
        self.assertEqual((b6["width"], b6["height"]), (1512, 2150))
        self.assertFalse(b6["capped"])
        press = page_pixels(182, 257, 600)
        self.assertEqual((press["width"], press["height"]), (3541, 5000))
        self.assertTrue(press["capped"])
        trade = resolve_format("western", "trade", "screen")
        self.assertEqual(trade["reading"], "ltr")
        self.assertEqual((trade["width"], trade["height"]), (864, 1296))
        manga = resolve_format("manga", "b6", "print")
        self.assertEqual(manga["reading"], "rtl")
        custom = resolve_format("western", "custom", "print", width=6, height=9, unit="in")
        self.assertEqual((custom["width"], custom["height"]), (1800, 2700))
        self.assertEqual(custom["unit"], "in")
        with self.assertRaises(ValueError):
            resolve_format("manga", "custom", "print", width=10, height=180, unit="mm")
        with self.assertRaises(ValueError):
            resolve_format("manga", "b6", "custom", dpi=40)
        with self.assertRaises(ValueError):
            resolve_format("western", "b6", "print")

        made = self.studio.canvas_prepare(
            {
                "create": True,
                "title": "Harbor",
                "kind": "western",
                "preset": "trade",
                "quality": "screen",
            }
        )
        project = made["tree"]["project"]
        self.assertEqual(project["title"], "Harbor")
        self.assertEqual(project["reading"], "ltr")
        self.assertEqual(project["pagePreset"], "trade")
        self.assertEqual(len(made["tree"]["chapters"][0]["pages"]), 1)
        board = next(item for item in made["mangaBoards"] if item["projectId"] == project["id"])
        self.assertEqual(board["pageKind"], "western")
        self.assertEqual(board["pageDpi"], 144)
        self.assertEqual((board["pageWidth"], board["pageHeight"]), (864, 1296))
        page = made["tree"]["chapters"][0]["pages"][0]
        exported = self.studio.export_page({"pageId": page["id"]})
        with Image.open(exported["path"]) as image:
            self.assertEqual(image.size, (864, 1296))

        again = self.studio.canvas_prepare(
            {
                "projectId": project["id"],
                "title": "Harbor",
                "kind": "manga",
                "preset": "b6",
                "quality": "print",
            }
        )
        self.assertEqual(len(again["tree"]["chapters"][0]["pages"]), 1)
        self.assertEqual(again["tree"]["project"]["reading"], "rtl")
        self.assertEqual(again["tree"]["project"]["pageWidth"], 1512)

        with self.assertRaises(ValueError):
            self.studio.canvas_prepare(
                {"create": True, "title": "  ", "kind": "manga", "preset": "b6", "quality": "print"}
            )

        square = self.studio.canvas_prepare(
            {
                "create": True,
                "title": "Square",
                "kind": "western",
                "preset": "custom",
                "quality": "print",
                "width": 200,
                "height": 200,
                "unit": "mm",
            }
        )
        square_page = square["tree"]["chapters"][0]["pages"][0]
        self.studio.page_update({"id": square_page["id"], "layout": "full"})
        preview = self.studio.panel_preview({"id": square_page["panels"][0]["id"]})
        self.assertEqual(preview["aspect"], "1:1")
        self.assertEqual(aspect_for_slot({"w": 1, "h": 1}, 1200 / 1800), "2:3")

    def test_existing_database_gains_page_format(self):
        from koma.formats import project_pixels

        path = Path(self.tmp.name) / "oldproj.sqlite"
        raw = sqlite3.connect(path)
        raw.execute(
            """CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                premise TEXT NOT NULL DEFAULT '',
                logline TEXT NOT NULL DEFAULT '',
                genre TEXT NOT NULL DEFAULT '',
                tone TEXT NOT NULL DEFAULT '',
                art_style TEXT NOT NULL DEFAULT '',
                style_notes TEXT NOT NULL DEFAULT '',
                reading TEXT NOT NULL DEFAULT 'rtl',
                pending_plan TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        raw.execute(
            "INSERT INTO projects (id, title, created_at, updated_at) VALUES ('p', 'Old', 't', 't')"
        )
        raw.commit()
        raw.close()
        store = Store(path)
        try:
            row = store.get_project("p")
            self.assertEqual(row["page_dpi"], 0)
            self.assertEqual(row["page_preset"], "")
            self.assertEqual(row["trim_w_mm"], 0)
            self.assertEqual(project_pixels(row), (1200, 1800))
        finally:
            store.close()

    def test_divide_volumes_suggest_and_bots(self):
        full = [{"x": 0, "y": 0, "w": 1, "h": 1}]
        slots, _index, axis, straightened = assist_divide(full, 0.5, 0.5, 0)
        self.assertEqual(axis, "h")
        self.assertFalse(straightened)
        self.assertAlmostEqual(slots[0]["h"] + slots[1]["h"], 1, places=3)
        _slots, _index, axis, _straightened = assist_divide(full, 0.5, 0.5, 80)
        self.assertEqual(axis, "v")
        _slots, _index, _axis, straightened = assist_divide(full, 0.5, 0.5, 12)
        self.assertTrue(straightened)
        _slots, _index, _axis, straightened = assist_divide(full, 0.5, 0.5, 4)
        self.assertFalse(straightened)
        with self.assertRaises(LayoutError):
            assist_divide([{"x": 0, "y": 0, "w": 1, "h": 0.1}], 0.5, 0.05, 0)

        created = self.studio.project_create({"title": "Harbor", "premise": "A quiet harbor."})
        project_id = created["tree"]["project"]["id"]
        self.assertEqual(created["tree"]["volumes"][0]["title"], "Volume 1")
        opened = self.studio.chapter_create({"projectId": project_id, "title": "Wake"})
        chapter = opened["tree"]["chapters"][0]
        self.assertEqual(len(opened["tree"]["volumes"]), 1)
        self.assertEqual(chapter["volumeId"], opened["tree"]["volumes"][0]["id"])
        self.studio.project_update({"id": project_id, "reading": "rtl"})
        made = self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})
        page = made["tree"]["chapters"][0]["pages"][0]
        divided = self.studio.page_divide({"id": page["id"], "x": 0.5, "y": 0.5, "angle": 4})
        self.assertEqual(divided["axis"], "h")
        self.assertFalse(divided["straightened"])
        custom = next(item for item in divided["tree"]["chapters"][0]["pages"] if item["id"] == page["id"])
        self.assertEqual(custom["layout"], "custom")
        self.assertEqual(len(custom["panels"]), 2)
        self.assertTrue(custom["slots"])
        self.assertTrue(self.store.get_page(page["id"])["slots_json"])
        self.assertAlmostEqual(custom["slots"][0]["h"] + custom["slots"][1]["h"], 1, places=2)

        fresh = self.studio.page_create({"chapterId": chapter["id"], "layout": "classic"})
        fresh_page = fresh["tree"]["chapters"][0]["pages"][-1]
        suggested = self.studio.page_suggest({"id": fresh_page["id"]})
        self.assertEqual(suggested["layoutId"], "classic")
        self.assertIn(suggested["layoutId"], {"full", "classic", "v3", "top-heavy", "bottom-heavy", "h2", "v2", "grid4", "inset", "grid6"})
        self.assertFalse(suggested["fromGrok"])

        bot = self.studio.bot_create({"projectId": project_id, "name": "Ada", "persona": "Keeps the story honest."})
        group = self.studio.group_create({"projectId": project_id, "title": "Room", "botIds": [bot["botId"]]})
        sent = self.studio.bot_send({"groupId": group["groupId"], "message": "What is open?", "view": "pages", "pageId": page["id"]})
        self.assertGreaterEqual(len(sent["messages"]), 2)
        self.assertEqual(sent["messages"][0]["author"], "user")
        empty = self.studio.group_create({"projectId": project_id, "title": "Empty", "botIds": []})
        with self.assertRaises(ValueError):
            self.studio.bot_send({"groupId": empty["groupId"], "message": "Hello"})

    def test_page_imagine_directs_empty_panels(self):
        created = self.studio.project_create({"title": "Imagine", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        made = self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})
        page = made["tree"]["chapters"][0]["pages"][0]
        person = self.studio.character_create({"projectId": project_id, "name": "Aki"})["tree"]["characters"][0]
        place = self.studio.location_create({"projectId": project_id, "name": "Pier"})["tree"]["locations"][0]
        image = Image.new("RGB", (8, 8), (1, 2, 3))
        path = Path(self.tmp.name) / "ref.png"
        image.save(path)
        closer = Image.new("RGB", (8, 8), (9, 9, 9))
        closer_path = Path(self.tmp.name) / "panel-ref.png"
        closer.save(closer_path)
        saved = self.studio.page_update({
            "id": page["id"],
            "prompt": "rain on the roof",
            "characterIds": [person["id"]],
            "locationId": place["id"],
        })
        page = saved["tree"]["chapters"][0]["pages"][0]
        self.assertEqual(page["prompt"], "rain on the roof")
        self.assertEqual(page["characterIds"], [person["id"]])
        self.assertEqual(page["locationId"], place["id"])
        self.studio.import_ref({"owner": "page", "id": page["id"], "kind": "reference", "path": str(path)})
        copied = self.studio.page_duplicate({"pageId": page["id"]})
        twin = next(item for item in copied["tree"]["chapters"][0]["pages"] if item["id"] == copied["pageId"])
        self.assertEqual(twin["prompt"], "rain on the roof")
        self.assertEqual(twin["characterIds"], [person["id"]])
        self.assertEqual(twin["locationId"], place["id"])
        self.assertEqual(len(twin["refs"]), 1)
        self.assertNotEqual(twin["refs"][0]["id"], "")
        page_ref = self.store.list_refs("page", page["id"])[0]["image_path"]
        copy_ref = self.store.list_refs("page", twin["id"])[0]["image_path"]
        self.assertNotEqual(page_ref, copy_ref)
        self.assertTrue(Path(page_ref).is_file())
        self.assertTrue(Path(copy_ref).is_file())

        drawn = self.studio.panel_draw_empty({"pageId": page["id"], "imagine": True})
        self.assertEqual(drawn["drawn"], 1)
        prompt, _aspect, refs = self.studio.client.prompts[-1]
        self.assertIn("rain on the roof", prompt)
        self.assertIn("Aki", prompt)
        self.assertIn("Pier", prompt)
        self.assertEqual(refs, [page_ref])

        panel_id = twin["panels"][0]["id"]
        self.studio.panel_update({"id": panel_id, "prompt": "close on the hands"})
        self.studio.import_ref({"owner": "panel", "id": panel_id, "kind": "reference", "path": str(closer_path)})
        panel_ref = self.store.list_refs("panel", panel_id)[0]["image_path"]
        again = self.studio.panel_draw_empty({"pageId": twin["id"], "imagine": True})
        self.assertEqual(again["drawn"], 1)
        prompt, _aspect, refs = self.studio.client.prompts[-1]
        self.assertIn("close on the hands", prompt)
        self.assertNotIn("rain on the roof", prompt)
        self.assertEqual(refs, [panel_ref])

        self.studio.panel_draw({"id": page["panels"][0]["id"]})
        prompt, _aspect, refs = self.studio.client.prompts[-1]
        self.assertNotIn("rain on the roof", prompt)
        self.assertEqual(refs, [])

        self.studio.page_delete({"id": page["id"]})
        self.assertFalse(Path(page_ref).exists())
        self.assertTrue(Path(copy_ref).is_file())

    def test_duplicate_keeps_panel_references(self):
        created = self.studio.project_create({"title": "Copy", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        page = self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})["tree"]["chapters"][0]["pages"][0]
        panel_id = page["panels"][0]["id"]
        picture = Path(self.tmp.name) / "panel-ref.png"
        Image.new("RGB", (8, 8), (3, 3, 3)).save(picture)
        self.studio.import_ref({"owner": "panel", "id": panel_id, "kind": "reference", "path": str(picture)})
        source_ref = self.store.list_refs("panel", panel_id)[0]["image_path"]
        copied = self.studio.page_duplicate({"pageId": page["id"]})
        twin = next(item for item in copied["tree"]["chapters"][0]["pages"] if item["id"] == copied["pageId"])
        self.assertEqual(len(twin["panels"][0]["refs"]), 1)
        copy_ref = self.store.list_refs("panel", twin["panels"][0]["id"])[0]["image_path"]
        self.assertNotEqual(copy_ref, source_ref)
        self.assertTrue(Path(copy_ref).is_file())
        self.studio.page_delete({"id": page["id"]})
        self.assertFalse(Path(source_ref).exists())
        self.assertTrue(Path(copy_ref).is_file())

    def test_delete_project_drops_rows_and_images(self):
        created = self.studio.project_create({"title": "Gone", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        page = self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})["tree"]["chapters"][0]["pages"][0]
        self.studio.panel_draw({"id": page["panels"][0]["id"]})
        picture = Path(self.tmp.name) / "keep.png"
        Image.new("RGB", (8, 8), (4, 5, 6)).save(picture)
        self.studio.import_ref({"owner": "page", "id": page["id"], "kind": "reference", "path": str(picture)})
        self.studio.import_ref({"owner": "panel", "id": page["panels"][0]["id"], "kind": "reference", "path": str(picture)})
        self.studio.page_sheet({"pageId": page["id"]})
        page_ref = self.store.list_refs("page", page["id"])[0]["image_path"]
        panel_ref = self.store.list_refs("panel", page["panels"][0]["id"])[0]["image_path"]
        panel_art = self.store.get_panel(page["panels"][0]["id"])["image_path"]
        preview = Path(os.environ["KOMA_DATA_DIR"]) / "previews" / f"{page['id']}.png"
        self.assertTrue(preview.is_file())
        self.studio.note_create({"projectId": project_id, "title": "Keep", "body": "x"})
        bot = self.studio.bot_create({"projectId": project_id, "name": "Ada", "persona": "Editor"})
        group = self.studio.group_create({"projectId": project_id, "title": "Room", "botIds": [bot["botId"]]})
        self.studio.bot_send({"groupId": group["groupId"], "message": "Hi"})
        self.assertGreater(len(self.store.list_volumes(project_id)), 0)
        kept = self.studio.project_create({"title": "Kept", "premise": ""})
        kept_id = kept["tree"]["project"]["id"]
        kept_chapter = self.studio.chapter_create({"projectId": kept_id})["tree"]["chapters"][0]
        self.studio.note_create({"projectId": kept_id, "title": "Stay", "body": "y"})
        removed = self.studio.project_delete({"id": project_id})
        self.assertEqual([item["id"] for item in removed["projects"]], [kept_id])
        self.assertEqual([item["projectId"] for item in removed["mangaBoards"]], [kept_id])
        self.assertFalse(Path(page_ref).exists())
        self.assertFalse(Path(panel_ref).exists())
        self.assertFalse(Path(panel_art).exists())
        self.assertFalse(preview.exists())
        self.assertEqual(self.store.list_projects(), [self.store.get_project(kept_id)])
        self.assertEqual(len(self.store.list_chapters(kept_id)), 1)
        self.assertEqual(self.store.list_chapters(kept_id)[0]["id"], kept_chapter["id"])
        self.assertEqual(len(self.store.list_notes(kept_id)), 1)
        self.assertEqual(self.store.list_volumes(kept_id)[0]["title"], "Volume 1")
        self.assertEqual(self.store._all("SELECT id FROM bots"), [])
        self.assertEqual(self.store._all("SELECT id FROM bot_groups"), [])
        self.assertEqual(self.store._all("SELECT id FROM bot_messages"), [])
        self.assertEqual(self.store._all("SELECT group_id FROM bot_members"), [])
        self.assertEqual(self.store._all("SELECT id FROM refs"), [])

    def test_reference_board_copies_a_local_picture(self):
        created = self.studio.project_create({"title": "Board", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        self.assertEqual(created["tree"]["moods"], [])
        other = self.studio.project_create({"title": "Other", "premise": ""})
        other_id = other["tree"]["project"]["id"]
        picture = Path(self.tmp.name) / "harbor-night.png"
        Image.new("RGB", (12, 8), (20, 40, 80)).save(picture)
        packed = self.studio.import_ref(
            {
                "owner": "project",
                "id": project_id,
                "path": str(picture),
                "kind": "portrait",
                "label": "  Harbor night  ",
            }
        )
        moods = packed["tree"]["moods"]
        self.assertEqual(len(moods), 1)
        self.assertEqual(moods[0]["kind"], "mood")
        self.assertEqual(moods[0]["label"], "Harbor night")
        self.assertTrue(str(moods[0]["url"]).startswith("koma:///"))
        stored = self.store.list_refs("project", project_id)[0]["image_path"]
        self.assertTrue(Path(stored).is_file())
        self.assertNotEqual(stored, str(picture))
        self.assertEqual(self.studio.project_open({"id": other_id})["tree"]["moods"], [])
        hits = self.studio.library_search({"query": "harbor"})
        self.assertEqual(len(hits["hits"]), 1)
        self.assertEqual(hits["hits"][0]["kind"], "mood")
        self.assertEqual(hits["hits"][0]["refId"], moods[0]["id"])
        self.assertEqual(hits["hits"][0]["projectId"], project_id)
        person = self.studio.character_create({"projectId": project_id, "name": "Aki"})["tree"]["characters"][0]
        face = Path(self.tmp.name) / "face.png"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(face)
        cast = self.studio.import_ref({"owner": "character", "id": person["id"], "path": str(face), "kind": "portrait"})
        portrait = cast["tree"]["characters"][0]["refs"][0]
        with self.assertRaises(ValueError):
            self.studio.mood_delete({"refId": portrait["id"]})
        with self.assertRaises(ValueError):
            self.studio.import_ref({"owner": "project", "id": project_id, "path": "/etc/hostname"})
        junk = Path(self.tmp.name) / "notes.txt"
        junk.write_text("nope", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.studio.import_ref({"owner": "project", "id": project_id, "path": str(junk)})
        unnamed = Path(self.tmp.name) / "street.png"
        Image.new("RGB", (4, 4), (9, 9, 9)).save(unnamed)
        named = self.studio.import_ref({"owner": "project", "id": project_id, "path": str(unnamed)})
        self.assertCountEqual([item["label"] for item in named["tree"]["moods"]], ["Harbor night", "street"])
        removed = self.studio.mood_delete({"refId": moods[0]["id"]})
        self.assertFalse(Path(stored).exists())
        self.assertEqual([item["label"] for item in removed["tree"]["moods"]], ["street"])
        street = self.store.list_refs("project", project_id)[0]["image_path"]
        portrait_path = self.store.list_refs("character", person["id"])[0]["image_path"]
        self.studio.project_delete({"id": project_id})
        self.assertFalse(Path(street).exists())
        self.assertFalse(Path(portrait_path).exists())
        self.assertEqual(self.store.list_refs("project", project_id), [])
        self.assertTrue(face.is_file())
        self.assertEqual(self.studio.project_open({"id": other_id})["tree"]["project"]["title"], "Other")

    def test_lettering_font_draws_japanese(self):
        from PIL import ImageDraw

        from koma.compose import font

        def raster(face, text: str) -> bytes:
            image = Image.new("L", (96, 72), 255)
            ImageDraw.Draw(image).text((6, 6), text, font=face, fill=0)
            return image.tobytes()

        for bold in (False, True):
            face = font(48, bold=bold)
            self.assertNotEqual(raster(face, "あ"), raster(face, "い"))

    def test_lettering_font_draws_latin(self):
        from PIL import ImageDraw

        from koma.compose import letter_font

        def raster(text: str) -> bytes:
            image = Image.new("L", (96, 72), 255)
            ImageDraw.Draw(image).text((6, 6), text, font=letter_font(48, True, text), fill=0)
            return image.tobytes()

        self.assertNotEqual(raster("K"), raster("A"))
        self.assertNotEqual(raster("あ"), raster("い"))

    def test_export_slug_keeps_unicode_titles_apart(self):
        from koma.paths import exports_dir, safe_slug

        tide = safe_slug("潮")
        harbor = safe_slug("港の夜")
        self.assertNotEqual(tide, harbor)
        self.assertNotEqual(tide, "manga")
        self.assertNotIn("/", safe_slug("a/b\\c"))
        self.assertEqual(safe_slug(""), "manga")
        self.assertEqual(safe_slug(".."), "manga")
        self.assertEqual(safe_slug("../etc"), "etc")
        renamed = self.studio.project_update({"id": self.studio.project_create({"title": "港の夜", "premise": ""})["tree"]["project"]["id"], "title": "潮"})
        self.assertEqual(renamed["mangaBoards"][0]["title"], "潮")
        folder = exports_dir("潮")
        self.assertEqual(folder.name, "潮")
        self.assertNotEqual(exports_dir("港の夜").name, folder.name)

    def test_delete_move_and_shrink_panels(self):
        from koma.config import save_config

        created = self.studio.project_create({"title": "Panels", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        made = self.studio.page_create({"chapterId": chapter["id"], "layout": "classic"})
        page = made["tree"]["chapters"][0]["pages"][0]
        panels = page["panels"]
        self.assertEqual(len(panels), 3)
        self.studio.panel_update({"id": panels[2]["id"], "dialogue": "待って", "sfx": "ドン"})
        with self.assertRaises(ValueError):
            self.studio.page_update({"id": page["id"], "layout": "full"})
        self.assertEqual(len(self.store.list_panels(page["id"])), 3)
        dropped = self.studio.page_update({"id": page["id"], "layout": "full", "dropExtra": True})
        left = next(item for item in dropped["tree"]["chapters"][0]["pages"] if item["id"] == page["id"])
        self.assertEqual(left["layout"], "full")
        self.assertEqual(len(left["panels"]), 1)
        self.assertEqual(len(self.store.list_panels(page["id"])), 1)

        wide = self.studio.page_create({"chapterId": chapter["id"], "layout": "v2"})
        page = wide["tree"]["chapters"][0]["pages"][-1]
        top, bottom = page["panels"]
        self.studio.panel_draw({"id": top["id"]})
        art = self.store.get_panel(top["id"])["image_path"]
        self.assertTrue(Path(art).is_file())
        save_config({"quality": "medium", "resolution": "2k"})
        self.studio.panel_draw({"id": bottom["id"]})
        self.assertEqual(self.studio.client.image_kwargs["quality"], "medium")
        self.assertEqual(self.studio.client.image_kwargs["resolution"], "2k")
        moved = self.studio.panel_move({"id": top["id"], "direction": 1})
        ordered = next(item for item in moved["tree"]["chapters"][0]["pages"] if item["id"] == page["id"])["panels"]
        self.assertEqual(ordered[1]["id"], top["id"])
        self.assertEqual(self.store.get_panel(top["id"])["slot"], 1)
        removed = self.studio.panel_delete({"id": top["id"]})
        survivor = next(item for item in removed["tree"]["chapters"][0]["pages"] if item["id"] == page["id"])
        self.assertEqual(survivor["layout"], "custom")
        self.assertEqual(len(survivor["panels"]), 1)
        self.assertEqual(survivor["panels"][0]["id"], bottom["id"])
        self.assertFalse(Path(art).exists())
        with self.assertRaises(ValueError):
            self.studio.panel_delete({"id": bottom["id"]})
        self.assertTrue(Path(self.store.get_panel(bottom["id"])["image_path"]).is_file())

    def test_undo_reflow_presets_and_cbz(self):
        import zipfile

        from koma.layouts import LAYOUTS, reflow_after_delete

        classic = [dict(slot) for slot in LAYOUTS["classic"][1]]
        grown = reflow_after_delete(classic, 1)
        self.assertEqual(len(grown), 2)
        right = max(grown, key=lambda slot: slot["x"])
        self.assertAlmostEqual(right["h"], 1, places=2)
        self.assertAlmostEqual(right["w"], 0.38, places=2)
        stacked = reflow_after_delete(classic, 0)
        self.assertEqual(len(stacked), 2)
        self.assertAlmostEqual(sum(slot["w"] * slot["h"] for slot in stacked), 1, places=2)

        created = self.studio.project_create({"title": "History", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        made = self.studio.page_create({"chapterId": chapter["id"], "layout": "classic"})
        page = made["tree"]["chapters"][0]["pages"][0]
        lead, second, _third = page["panels"]
        self.studio.panel_update({"id": lead["id"], "dialogue": "走って", "balloon": "round"})
        self.studio.panel_draw({"id": lead["id"]})
        self.studio.panel_update({"id": lead["id"], "balloon": "shout", "balloonAnchor": "tr", "sfx": "ドン"})
        lettered = self.studio.page_undo({"pageId": page["id"]})
        undone = next(item for item in lettered["tree"]["chapters"][0]["pages"] if item["id"] == page["id"])
        restored_lead = next(item for item in undone["panels"] if item["id"] == lead["id"])
        self.assertEqual(restored_lead["balloon"], "round")
        self.assertEqual(restored_lead["dialogue"], "走って")
        self.assertEqual(restored_lead["sfx"], "")
        self.studio.page_redo({"pageId": page["id"]})
        self.studio.panel_delete({"id": second["id"]})
        shrunk = self.studio.page_undo({"pageId": page["id"]})
        back = next(item for item in shrunk["tree"]["chapters"][0]["pages"] if item["id"] == page["id"])
        self.assertEqual(back["layout"], "classic")
        self.assertEqual(len(back["panels"]), 3)
        self.assertTrue(Path(self.store.get_panel(lead["id"])["image_path"]).is_file())
        self.studio.panel_update({"id": page["panels"][2]["id"], "dialogue": "待って"})
        self.studio.page_update({"id": page["id"], "layout": "full", "dropExtra": True})
        self.assertEqual(len(self.store.list_panels(page["id"])), 1)
        opened = self.studio.page_undo({"pageId": page["id"]})
        full = next(item for item in opened["tree"]["chapters"][0]["pages"] if item["id"] == page["id"])
        self.assertEqual(full["layout"], "classic")
        self.assertEqual(len(full["panels"]), 3)

        cbz = self.studio.export_chapter({"chapterId": chapter["id"], "format": "cbz"})
        self.assertTrue(cbz["path"].endswith(".cbz"))
        with zipfile.ZipFile(cbz["path"]) as archive:
            names = archive.namelist()
        self.assertEqual(names, ["001.png"])
        self.assertGreater(Path(cbz["path"]).stat().st_size, 100)

    def test_chapter_pdf_is_finish_size(self):
        import re

        prepared = self.studio.canvas_prepare(
            {"title": "Bound", "kind": "manga", "preset": "b6", "quality": "print", "create": True}
        )
        project = prepared["tree"]["project"]
        chapter = prepared["tree"]["chapters"][0]
        self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})
        exported = self.studio.export_chapter({"chapterId": chapter["id"], "format": "pdf"})
        raw = Path(exported["path"]).read_bytes().decode("latin1")
        boxes = re.findall(r"/MediaBox\s*\[([^\]]+)\]", raw)
        self.assertEqual(len(boxes), 2)
        numbers = [float(part) for part in boxes[0].split()]
        self.assertEqual(numbers[0], 0)
        self.assertEqual(numbers[1], 0)
        width_pt, height_pt = numbers[2], numbers[3]
        self.assertAlmostEqual(width_pt, project["trimW"] / 25.4 * 72, delta=1)
        self.assertAlmostEqual(height_pt, project["trimH"] / 25.4 * 72, delta=1)
        self.assertLess(width_pt, project["pageWidth"] / 2)

    def test_unreadable_export_image_is_not_a_read_only_folder(self):
        from PIL import UnidentifiedImageError
        from unittest.mock import patch

        prepared = self.studio.canvas_prepare(
            {"title": "Loose", "kind": "manga", "preset": "b6", "quality": "print", "create": True}
        )
        chapter = prepared["tree"]["chapters"][0]
        self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})
        real_open = Image.open

        def opened(path, *args, **kwargs):
            name = Path(str(path)).name
            if name.startswith("ch") and name.endswith(".png"):
                raise UnidentifiedImageError("not a png")
            return real_open(path, *args, **kwargs)

        with patch("PIL.Image.open", opened):
            with self.assertRaises(ValueError) as raised:
                self.studio.export_chapter({"chapterId": chapter["id"], "format": "pdf"})
        message = str(raised.exception)
        self.assertIn("could not read", message.lower())
        self.assertNotIn("read-only", message.lower())

        exported = self.studio.export_chapter({"chapterId": chapter["id"], "format": "pdf"})
        self.assertTrue(exported["path"].endswith(".pdf"))
        self.assertTrue(Path(exported["path"]).is_file())

        blocker = Path(self.tmp.name) / "export-blocked-pdf"
        blocker.write_text("nope", encoding="utf-8")
        os.environ["KOMA_EXPORT_DIR"] = str(blocker)
        try:
            with self.assertRaises(ValueError) as blocked:
                self.studio.export_chapter({"chapterId": chapter["id"], "format": "pdf"})
            self.assertIn("read-only", str(blocked.exception).lower())
        finally:
            os.environ["KOMA_EXPORT_DIR"] = str(Path(self.tmp.name) / "export")

    def test_search_duplicate_chapter_and_failures(self):
        import urllib.error
        from unittest.mock import patch

        from koma.ai import AIError, Client
        from koma.service import ROUTES
        from koma.store import StoreError

        self.assertIn("library.search", ROUTES)
        self.assertIn("chapter.duplicate", ROUTES)
        prepared = self.studio.canvas_prepare(
            {
                "title": "港🌙" + ("あ" * 200),
                "kind": "manga",
                "preset": "b6",
                "quality": "print",
                "create": True,
            }
        )
        project = prepared["tree"]["project"]
        self.assertEqual(len(project["title"]), 120)
        self.assertTrue(project["title"].startswith("港🌙"))
        project_id = project["id"]
        with self.assertRaises(ValueError) as raised:
            self.studio.project_update({"id": project_id, "title": "   "})
        self.assertIn("Name the manga.", str(raised.exception))
        chapter = prepared["tree"]["chapters"][0]
        page = chapter["pages"][0]
        panel = page["panels"][0]
        with self.assertRaises(ValueError) as raised:
            self.studio.chapter_update({"id": chapter["id"], "title": " "})
        self.assertIn("Name the chapter.", str(raised.exception))
        self.studio.chapter_update({"id": chapter["id"], "title": "章" * 300})
        self.assertEqual(len(self.store.get_chapter(chapter["id"])["title"]), 160)
        person = self.studio.character_create({"projectId": project_id})
        char_id = person["tree"]["characters"][-1]["id"]
        with self.assertRaises(ValueError) as raised:
            self.studio.character_update({"id": char_id, "name": " "})
        self.assertIn("Name the character.", str(raised.exception))

        self.studio.chapter_update({"id": chapter["id"], "title": "夜の港"})
        self.studio.panel_update({"id": panel["id"], "dialogue": "こんにちは🎉"})
        found = self.studio.library_search({"query": "🎉"})
        self.assertTrue(any(hit["kind"] == "dialogue" and hit["panelId"] == panel["id"] for hit in found["hits"]))
        named = self.studio.library_search({"query": "夜の港"})
        self.assertTrue(any(hit["kind"] == "chapter" and hit["chapterId"] == chapter["id"] for hit in named["hits"]))
        other = self.studio.project_create({"title": "Quiet", "premise": ""})
        other_id = other["tree"]["project"]["id"]
        self.assertFalse(any(hit["projectId"] == other_id for hit in self.studio.library_search({"query": "🎉"})["hits"]))
        self.assertEqual(self.studio.library_search({"query": "  "}), {"hits": []})

        copied = self.studio.chapter_duplicate({"id": chapter["id"]})
        order = [item["id"] for item in copied["tree"]["chapters"]]
        self.assertEqual(order[order.index(chapter["id"]) + 1], copied["chapterId"])
        twin = next(item for item in copied["tree"]["chapters"] if item["id"] == copied["chapterId"])
        self.assertIn("copy", twin["title"])
        self.assertEqual(len(twin["pages"]), len(chapter["pages"]))
        self.assertNotEqual(twin["pages"][0]["id"], page["id"])
        self.assertEqual(twin["pages"][0]["panels"][0]["dialogue"], "こんにちは🎉")

        missing = Path(self.tmp.name) / "gone.png"
        self.store.update_panel(panel["id"], {"image_path": str(missing)}, {"image_path"})
        exported = self.studio.export_page({"pageId": page["id"]})
        self.assertTrue(Path(exported["path"]).is_file())
        bad = Path(self.tmp.name) / "bad.png"
        bad.write_bytes(b"not a png")
        self.store.update_panel(panel["id"], {"image_path": str(bad)}, {"image_path"})
        exported = self.studio.export_page({"pageId": page["id"]})
        with Image.open(exported["path"]) as image:
            self.assertEqual(image.format, "PNG")

        blocker = Path(self.tmp.name) / "export-blocked"
        blocker.write_text("nope", encoding="utf-8")
        os.environ["KOMA_EXPORT_DIR"] = str(blocker)
        try:
            with self.assertRaises(ValueError) as raised:
                self.studio.export_page({"pageId": page["id"]})
            self.assertIn("cannot write", str(raised.exception).lower())
        finally:
            os.environ["KOMA_EXPORT_DIR"] = str(Path(self.tmp.name) / "export")

        junk = Path(self.tmp.name) / "junk.sqlite"
        junk.write_bytes(b"this is not a sqlite database")
        with self.assertRaises(StoreError) as raised:
            Store(junk)
        self.assertIn("damaged", str(raised.exception).lower())

        lock = sqlite3.connect(str(Path(self.tmp.name) / "studio.sqlite"), timeout=2)
        lock.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(ValueError) as raised:
                self.studio.project_update({"id": project_id, "logline": "locked"})
            self.assertIn("another", str(raised.exception).lower())
        finally:
            lock.rollback()
            lock.close()

        with self.assertRaises(AIError) as raised:
            Client(lambda: "").imagine("a pier", "2:3", [], resolution="1k")
        self.assertIn("API key", str(raised.exception))
        with patch("koma.ai.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(AIError) as raised:
                Client(lambda: "secret-key-xyz").complete([{"role": "user", "content": "hi"}])
        self.assertIn("Could not reach", str(raised.exception))
        self.assertNotIn("secret-key-xyz", str(raised.exception))

    def test_reorder_saved_undo_and_wider_search(self):
        from koma.paths import data_dir
        from koma.service import ROUTES

        self.assertIn("page.reorder", ROUTES)
        self.assertIn("panel.reorder", ROUTES)
        created = self.studio.project_create({"title": "Order", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        self.studio.page_create({"chapterId": chapter["id"], "layout": "classic"})
        made = self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})
        pages = made["tree"]["chapters"][0]["pages"]
        classic_id, full_id = pages[0]["id"], pages[1]["id"]
        flipped = self.studio.page_reorder({"chapterId": chapter["id"], "ids": [full_id, classic_id]})
        ordered = flipped["tree"]["chapters"][0]["pages"]
        self.assertEqual([item["id"] for item in ordered], [full_id, classic_id])
        classic = next(item for item in ordered if item["id"] == classic_id)
        ids = [item["id"] for item in classic["panels"]]
        moved = self.studio.panel_reorder({"pageId": classic_id, "ids": [ids[2], ids[0], ids[1]]})
        after = next(item for item in moved["tree"]["chapters"][0]["pages"] if item["id"] == classic_id)
        self.assertEqual([item["id"] for item in after["panels"]], [ids[2], ids[0], ids[1]])
        self.studio.page_undo({"pageId": classic_id})
        restored = next(item for item in self.studio.page_redo({"pageId": classic_id})["tree"]["chapters"][0]["pages"] if item["id"] == classic_id)
        self.assertEqual([item["id"] for item in restored["panels"]], [ids[2], ids[0], ids[1]])

        lead = restored["panels"][0]["id"]
        for index in range(25):
            self.studio.panel_update({"id": lead, "dialogue": f"line {index}"})
        history = data_dir() / "history" / f"{classic_id}.json"
        raw = json.loads(history.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["undo"]), 20)
        self.assertLess(history.stat().st_size, 80_000)
        fresh = Studio(self.store, client=FakeClient())
        undone = fresh.page_undo({"pageId": classic_id})
        lead_row = next(
            item
            for item in next(page for page in undone["tree"]["chapters"][0]["pages"] if page["id"] == classic_id)["panels"]
            if item["id"] == lead
        )
        self.assertEqual(lead_row["dialogue"], "line 23")

        full = next(item for item in undone["tree"]["chapters"][0]["pages"] if item["id"] == full_id)
        drawn_id = full["panels"][0]["id"]
        self.studio.panel_draw({"id": drawn_id})
        self.studio.panel_update({"id": drawn_id, "dialogue": "kept"})
        old = self.store.get_panel(drawn_id)["image_path"]
        Path(old).unlink()
        again = Studio(self.store, client=FakeClient())
        again.page_undo({"pageId": full_id})
        panel = self.store.get_panel(drawn_id)
        self.assertEqual(panel["dialogue"], "")
        self.assertTrue(Path(panel["image_path"]).is_file())
        self.assertNotEqual(panel["image_path"], old)

        person = self.studio.character_create({"projectId": project_id})
        char_id = person["tree"]["characters"][-1]["id"]
        self.studio.character_update({"id": char_id, "name": "Aki🌙", "role": "runner"})
        note = self.studio.note_create({"projectId": project_id, "title": "Pier", "body": "wet boards"})
        other = self.studio.project_create({"title": "Quiet", "premise": ""})
        cast_hits = self.studio.library_search({"query": "AKI"})
        self.assertTrue(any(hit["kind"] == "cast" and hit["characterId"] == char_id for hit in cast_hits["hits"]))
        note_hits = self.studio.library_search({"query": "wet boards"})
        self.assertTrue(any(hit["kind"] == "note" and hit["noteId"] == note["noteId"] for hit in note_hits["hits"]))
        self.assertFalse(any(hit["projectId"] == other["tree"]["project"]["id"] for hit in note_hits["hits"]))
        self.studio.page_delete({"id": classic_id})
        self.assertFalse(history.exists())

    def test_mesh_storage_export_and_delete(self):
        from koma.mesh import cube_glb, obj_from_glb, parse_glb

        def backend(provider, prompt, images, status, settings):
            self.assertEqual(provider, "local")
            self.assertIn("runner", prompt)
            status("Building the 3D model…")
            return cube_glb()

        self.studio.mesh_backend = backend
        created = self.studio.project_create({"title": "Figures", "premise": "A cast with models."})
        project_id = created["tree"]["project"]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Aki"})
        character_id = made["tree"]["characters"][-1]["id"]
        with self.assertRaises(ValueError) as missing:
            self.studio.character_mesh({"id": character_id, "source": "refs", "prompt": "from the sheet"})
        self.assertIn("portrait", str(missing.exception).lower())

        built = self.studio.character_mesh({"id": character_id, "source": "prompt", "prompt": "a runner in a red jacket"})
        person = built["tree"]["characters"][-1]
        self.assertEqual(len(person["meshes"]), 1)
        mesh = person["meshes"][0]
        self.assertEqual(mesh["provider"], "local")
        self.assertEqual(mesh["source"], "prompt")
        self.assertTrue(mesh["url"].startswith("koma:///mesh/"))
        self.assertTrue(mesh["url"].endswith(".glb"))
        row = self.store.get_mesh(mesh["id"])
        self.assertTrue(Path(row["glb_path"]).is_file())
        positions, indices = parse_glb(Path(row["glb_path"]).read_bytes())
        self.assertGreaterEqual(len(positions), 8)
        self.assertGreaterEqual(len(indices), 36)
        obj = obj_from_glb(Path(row["glb_path"]).read_bytes())
        self.assertIn(b"\nv ", b"\n" + obj)
        self.assertIn(b"\nf ", b"\n" + obj)

        exported = self.studio.character_mesh_export({"id": mesh["id"], "format": "glb"})
        obj_export = self.studio.character_mesh_export({"id": mesh["id"], "format": "obj"})
        self.assertTrue(exported["path"].endswith("Aki-model.glb"))
        self.assertTrue(Path(exported["path"]).is_file())
        self.assertTrue(Path(obj_export["path"]).read_bytes().startswith(b"# Koma"))
        preview = self.studio.character_mesh_bytes({"id": mesh["id"]})
        self.assertTrue(preview["glb"])
        with self.assertRaises(ValueError):
            self.studio.character_mesh_export({"id": mesh["id"], "format": "stl"})

        self.studio.mesh_backend = lambda *_args: b"not-a-glb"
        with self.assertRaises(ValueError) as bad:
            self.studio.character_mesh({"id": character_id, "source": "prompt", "prompt": "again"})
        self.assertIn("GLB", str(bad.exception))

        glb_path = row["glb_path"]
        self.studio.character_delete({"id": character_id})
        self.assertFalse(Path(glb_path).exists())
        self.assertEqual(self.store.list_meshes(character_id), [])

        again = self.studio.character_create({"projectId": project_id, "name": "Ren"})
        self.studio.mesh_backend = backend
        ren_id = again["tree"]["characters"][-1]["id"]
        held = self.studio.character_mesh({"id": ren_id, "source": "prompt", "prompt": "a runner"})
        held_path = self.store.get_mesh(held["tree"]["characters"][-1]["meshes"][0]["id"])["glb_path"]
        self.studio.project_delete({"id": project_id})
        self.assertFalse(Path(held_path).exists())
        self.assertEqual(self.store._all("SELECT id FROM meshes", ()), [])
        self.assertEqual(self.store._all("SELECT id FROM characters", ()), [])

    def test_mesh_provider_selection_and_errors(self):
        from koma.mesh import NOT_INSTALLED, MeshError, cube_glb, generate_mesh, glb_from_obj, obj_from_glb

        created = self.studio.project_create({"title": "Providers", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Aki"})
        character_id = made["tree"]["characters"][-1]["id"]
        settings = self.studio.bootstrap({})["settings"]
        self.assertEqual(settings["meshProvider"], "local")
        self.assertFalse(settings["localReady"])
        self.assertFalse(settings["hasMeshyKey"])
        self.assertFalse(settings["hasTripoKey"])

        with self.assertRaises(MeshError) as missing_local:
            self.studio.character_mesh({"id": character_id, "source": "prompt", "prompt": "a runner"})
        self.assertIn("not installed", str(missing_local.exception))
        self.assertIn("TripoSR", str(missing_local.exception))
        self.assertEqual(NOT_INSTALLED[:20], str(missing_local.exception)[:20])

        saved = self.studio.settings_save({"meshyKey": "meshy-secret-key-99"})
        self.assertTrue(saved["settings"]["hasMeshyKey"])
        self.assertEqual(saved["settings"]["meshyHint"], "…y-99")
        self.assertNotIn("meshy-secret-key-99", json.dumps(saved["settings"]))
        self.studio.settings_save({"clearMeshyKey": True, "tripoKey": "tripo-secret-key-42", "meshProvider": "tripo"})
        cleared = self.studio.bootstrap({})["settings"]
        self.assertFalse(cleared["hasMeshyKey"])
        self.assertTrue(cleared["hasTripoKey"])
        self.assertEqual(cleared["meshProvider"], "tripo")
        self.assertNotIn("tripo-secret-key-42", json.dumps(cleared))

        with self.assertRaises(MeshError) as no_meshy:
            generate_mesh("meshy", "a chair", [], lambda _text: None, {"meshy_key": ""}, transport=self.fail_transport, pause=0)
        self.assertIn("Meshy API key", str(no_meshy.exception))
        with self.assertRaises(MeshError) as no_tripo:
            generate_mesh("tripo", "a chair", [], lambda _text: None, {"tripo_key": ""}, transport=self.fail_transport, pause=0)
        self.assertIn("Tripo API key", str(no_tripo.exception))
        with self.assertRaises(MeshError):
            generate_mesh("unknown", "a chair", [], lambda _text: None, {}, pause=0)

        secret = "meshy-secret-key-99"
        leaked = {}

        def refuse(method, url, headers, body):
            leaked["auth"] = headers.get("Authorization", "")
            return 401, f"denied {secret}".encode()

        with self.assertRaises(MeshError) as refused:
            generate_mesh("meshy", "a chair", [], lambda _text: None, {"meshy_key": secret}, transport=refuse, pause=0)
        self.assertNotIn(secret, str(refused.exception))
        self.assertIn(secret, leaked["auth"])

        calls = []

        def meshy_text(method, url, headers, body):
            calls.append((method, url))
            if method == "POST" and url.endswith("/text-to-3d") and body and b'"preview"' in body:
                return 200, b'{"result":"preview-1"}'
            if method == "GET" and url.endswith("/preview-1"):
                return 200, b'{"status":"SUCCEEDED"}'
            if method == "POST" and b'"refine"' in (body or b""):
                return 200, b'{"result":"refine-1"}'
            if method == "GET" and url.endswith("/refine-1"):
                return 200, json.dumps({"status": "SUCCEEDED", "model_urls": {"glb": "https://assets.meshy.ai/model.glb"}}).encode()
            if url == "https://assets.meshy.ai/model.glb":
                return 200, cube_glb()
            raise AssertionError(method + " " + url)

        glb = generate_mesh("meshy", "a wooden chair", [], lambda _text: None, {"meshy_key": secret}, transport=meshy_text, pause=0)
        self.assertTrue(glb.startswith(b"glTF"))
        self.assertEqual(sum(1 for method, _url in calls if method == "POST"), 2)

        def meshy_image(method, url, headers, body):
            if method == "POST":
                self.assertIn(b"image_url", body)
                return 200, b'{"result":"image-1"}'
            if method == "GET" and url.endswith("/image-1"):
                return 200, json.dumps({"status": "FAILED"}).encode()
            raise AssertionError(url)

        image = self.tmp.name + "/ref.png"
        Image.new("RGB", (8, 8), (10, 20, 30)).save(image)
        with self.assertRaises(MeshError) as failed:
            generate_mesh("meshy", "pose", [image], lambda _text: None, {"meshy_key": secret}, transport=meshy_image, pause=0)
        self.assertIn("could not build", str(failed.exception))

        def tripo_ok(method, url, headers, body):
            if url.endswith("/upload"):
                self.assertIn(b"reference.png", body)
                return 200, b'{"code":0,"data":{"image_token":"tok-1"}}'
            if method == "POST" and url.endswith("/task"):
                self.assertIn(b"tok-1", body)
                return 200, b'{"code":0,"data":{"task_id":"task-1"}}'
            if method == "GET" and url.endswith("/task-1"):
                return 200, json.dumps({"code": 0, "data": {"status": "success", "output": {"model": "https://api.tripo3d.ai/dl/model.glb"}}}).encode()
            if url == "https://api.tripo3d.ai/dl/model.glb":
                return 200, cube_glb()
            raise AssertionError(url)

        lifted = generate_mesh("tripo", "pose", [image], lambda _text: None, {"tripo_key": "tripo-secret-key-42"}, transport=tripo_ok, pause=0)
        self.assertTrue(lifted.startswith(b"glTF"))

        def http_link(method, url, headers, body):
            if method == "POST":
                return 200, b'{"code":0,"data":{"task_id":"task-2"}}'
            return 200, b'{"code":0,"data":{"status":"success","output":{"model":"http://api.tripo3d.ai/model.glb"}}}'

        with self.assertRaises(MeshError) as insecure:
            generate_mesh("tripo", "pose", [], lambda _text: None, {"tripo_key": "k"}, transport=http_link, pause=0)
        self.assertIn("HTTPS", str(insecure.exception))

        script = Path(self.tmp.name) / "backend.py"
        obj_script = Path(self.tmp.name) / "obj_backend.py"
        cube_path = Path(self.tmp.name) / "cube.glb"
        cube_path.write_bytes(cube_glb())
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            f"src = Path({str(cube_path)!r})\n"
            "out = Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "out.write_bytes(src.read_bytes())\n",
            encoding="utf-8",
        )
        obj_script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            "out = Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "out.write_text('v 0 0 0\\nv 1 0 0\\nv 0 1 0\\nf 1 2 3\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        obj_script.chmod(0o755)
        ready = self.studio.settings_save({"meshProvider": "local", "meshCommand": str(script)})
        self.assertTrue(ready["settings"]["localReady"])
        local = generate_mesh("local", "ignored", [image], lambda _text: None, {"mesh_command": str(script)}, pause=0)
        self.assertTrue(local.startswith(b"glTF"))
        converted = generate_mesh("local", "ignored", [image], lambda _text: None, {"mesh_command": str(obj_script)}, pause=0)
        self.assertEqual(len(parse_positions(converted)), 3)
        broken = Path(self.tmp.name) / "broken.py"
        broken.write_text("#!/usr/bin/env python3\nimport sys\nsys.stderr.write('torch missing\\n')\nraise SystemExit(1)\n", encoding="utf-8")
        broken.chmod(0o755)
        with self.assertRaises(MeshError) as crashed:
            generate_mesh("local", "ignored", [image], lambda _text: None, {"mesh_command": str(broken)}, pause=0)
        self.assertIn("torch missing", str(crashed.exception))
        self.assertNotIn("tripo-secret", str(crashed.exception))
        roundtrip = glb_from_obj(obj_from_glb(cube_glb()).decode("utf-8"))
        self.assertTrue(roundtrip.startswith(b"glTF"))

    def test_mesh_import_glb_and_obj(self):
        from koma.mesh import cube_glb, obj_from_glb, parse_glb

        created = self.studio.project_create({"title": "Imports", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Aki"})
        character_id = made["tree"]["characters"][-1]["id"]
        folder = Path(self.tmp.name)
        glb_path = folder / "sample.glb"
        obj_path = folder / "sample.obj"
        glb_path.write_bytes(cube_glb())
        obj_path.write_text(obj_from_glb(cube_glb()).decode("utf-8"), encoding="utf-8")

        imported = self.studio.character_mesh_import({"id": character_id, "path": str(glb_path)})
        mesh = imported["tree"]["characters"][-1]["meshes"][0]
        self.assertEqual(mesh["provider"], "import")
        self.assertEqual(mesh["source"], "file")
        self.assertEqual(mesh["prompt"], "sample.glb")
        stored = Path(self.store.get_mesh(mesh["id"])["glb_path"])
        self.assertTrue(stored.is_file())
        positions, indices = parse_glb(stored.read_bytes())
        self.assertGreaterEqual(len(positions), 8)
        self.assertGreaterEqual(len(indices), 36)

        from_obj = self.studio.character_mesh_import({"id": character_id, "path": str(obj_path)})
        second = from_obj["tree"]["characters"][-1]["meshes"][0]
        self.assertEqual(second["prompt"], "sample.obj")
        self.assertEqual(len(from_obj["tree"]["characters"][-1]["meshes"]), 2)
        obj_positions, _obj_indices = parse_glb(Path(self.store.get_mesh(second["id"])["glb_path"]).read_bytes())
        self.assertGreaterEqual(len(obj_positions), 8)

        outside = Path("/etc/hostname")
        if outside.is_file():
            with self.assertRaises(ValueError) as blocked:
                self.studio.character_mesh_import({"id": character_id, "path": str(outside)})
            self.assertIn("home folder", str(blocked.exception))
        junk = folder / "notes.glb"
        junk.write_bytes(b"not a mesh")
        with self.assertRaises(ValueError) as bad:
            self.studio.character_mesh_import({"id": character_id, "path": str(junk)})
        self.assertIn("GLB", str(bad.exception))
        empty = folder / "empty.obj"
        empty.write_text("v 0 0 0\n", encoding="utf-8")
        with self.assertRaises(ValueError) as no_faces:
            self.studio.character_mesh_import({"id": character_id, "path": str(empty)})
        self.assertIn("faces", str(no_faces.exception).lower())
        text = folder / "readme.txt"
        text.write_text("hello", encoding="utf-8")
        with self.assertRaises(ValueError) as kind:
            self.studio.character_mesh_import({"id": character_id, "path": str(text)})
        self.assertIn(".glb", str(kind.exception))

        self.studio.character_delete({"id": character_id})
        self.assertFalse(stored.exists())
        self.assertEqual(self.store.list_meshes(character_id), [])

    def test_mesh_import_vrm_textures_and_placed_parts(self):
        import base64

        from koma.mesh import MeshError, _assemble, _chunks, cube_glb, load_model_file, parse_glb

        created = self.studio.project_create({"title": "Figures", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Shino"})
        character_id = made["tree"]["characters"][-1]["id"]
        folder = Path(self.tmp.name) / "pack"
        folder.mkdir()
        png = folder / "skin.png"
        Image.new("RGB", (2, 2), (20, 180, 40)).save(png, format="PNG")
        document, blob = _chunks(cube_glb())
        document["nodes"] = [
            {"children": [1], "translation": [0, 2, 0]},
            {"mesh": 0, "translation": [0, 3, 0]},
        ]
        document["images"] = [{"uri": "skin.png"}]
        document["textures"] = [{"source": 0}]
        document["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
        document["meshes"][0]["primitives"][0]["material"] = 0
        placed = folder / "placed.glb"
        placed.write_bytes(_assemble(document, blob))

        loaded = load_model_file(placed)
        positions, _indices = parse_glb(loaded)
        self.assertGreater(min(point[1] for point in positions), 4)
        baked, _baked_blob = _chunks(loaded)
        self.assertNotIn("translation", baked["nodes"][0])
        self.assertNotIn("translation", baked["nodes"][1])
        self.assertNotIn("uri", baked["images"][0])
        self.assertIn(b"\x89PNG", loaded)

        imported = self.studio.character_mesh_import({"id": character_id, "path": str(placed)})
        mesh = imported["tree"]["characters"][-1]["meshes"][0]
        self.assertEqual(mesh["prompt"], "placed.glb")
        part = self.studio.character_mesh_bytes({"id": mesh["id"], "offset": 0})
        self.assertEqual(part["total"], Path(self.store.get_mesh(mesh["id"])["glb_path"]).stat().st_size)
        self.assertTrue(base64.b64decode(part["glb"]).startswith(b"glTF"))

        vrm = folder / "hero.vrm"
        vrm.write_bytes(cube_glb())
        again = self.studio.character_mesh_import({"id": character_id, "path": str(vrm)})
        self.assertEqual(again["tree"]["characters"][-1]["meshes"][0]["prompt"], "hero.vrm")
        bad = folder / "notes.vrm"
        bad.write_bytes(b"not a vrm")
        with self.assertRaises(ValueError) as broken:
            self.studio.character_mesh_import({"id": character_id, "path": str(bad)})
        self.assertIn("GLB", str(broken.exception))

        outside = folder / "escape.glb"
        escaped, escaped_blob = _chunks(cube_glb())
        escaped["images"] = [{"uri": "../secret.png"}]
        outside.write_bytes(_assemble(escaped, escaped_blob))
        secret = Path(self.tmp.name) / "secret.png"
        secret.write_bytes(b"\x89PNG-secret")
        with self.assertRaises(MeshError) as leaked:
            load_model_file(outside)
        self.assertIn("outside", str(leaked.exception))
        remote, remote_blob = _chunks(cube_glb())
        remote["images"] = [{"uri": "https://example.com/skin.png"}]
        web = folder / "web.glb"
        web.write_bytes(_assemble(remote, remote_blob))
        with self.assertRaises(MeshError) as online:
            load_model_file(web)
        self.assertIn("web", str(online.exception))

        (folder / "hero.mtl").write_text("newmtl skin\nKd 1 0 0\nmap_Kd skin.png\n", encoding="utf-8")
        (folder / "hero.obj").write_text(
            "mtllib hero.mtl\nusemtl skin\n"
            "v 0 0 0 1 1 1\nv 1 0 0\nv 0 1 0\n"
            "vt 0 0\nvt 1 0\nvt 0 1\n"
            "f 1/1/1 2/2/1 3/3/1\n",
            encoding="utf-8",
        )
        obj_import = self.studio.character_mesh_import({"id": character_id, "path": str(folder / "hero.obj")})
        obj_mesh = obj_import["tree"]["characters"][-1]["meshes"][0]
        self.assertEqual(obj_mesh["prompt"], "hero.obj")
        stored = Path(self.store.get_mesh(obj_mesh["id"])["glb_path"]).read_bytes()
        obj_doc, _obj_blob = _chunks(stored)
        primitive = obj_doc["meshes"][0]["primitives"][0]
        self.assertIn("TEXCOORD_0", primitive["attributes"])
        self.assertNotIn("uri", obj_doc["images"][0])
        self.assertIn(b"\x89PNG", stored)

        obj_path = self.store.get_mesh(obj_mesh["id"])["glb_path"]
        self.studio.project_delete({"id": project_id})
        self.assertFalse(Path(obj_path).exists())
        self.assertEqual(self.store.list_meshes(character_id), [])

    def test_mesh_expression_snapshot_and_thumbnail(self):
        import base64
        import struct

        from koma.mesh import _assemble, _chunks, apply_morph, blend_shape_names, cube_glb, read_morph

        document, blob = _chunks(cube_glb())
        deltas = b"".join(struct.pack("<3f", 0.0, 0.5, 0.0) for _ in range(8))
        start = len(blob)
        blob = blob + deltas
        document["buffers"][0]["byteLength"] = len(blob)
        document["bufferViews"].append({"buffer": 0, "byteOffset": start, "byteLength": len(deltas)})
        document["accessors"].append(
            {"bufferView": len(document["bufferViews"]) - 1, "componentType": 5126, "count": 8, "type": "VEC3"}
        )
        document["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": len(document["accessors"]) - 1}]
        document["meshes"][0]["extras"] = {"targetNames": ["Blink"]}
        document["extensions"] = {
            "VRM": {
                "blendShapeMaster": {
                    "blendShapeGroups": [
                        {"name": "Neutral", "presetName": "neutral", "binds": [{"mesh": 0, "index": 0, "weight": 0}]},
                        {"name": "Blink", "presetName": "blink", "binds": [{"mesh": 0, "index": 0, "weight": 100}]},
                    ]
                }
            }
        }
        morphed = _assemble(document, blob)
        self.assertEqual(blend_shape_names(morphed), ["Blink"])
        self.assertEqual(blend_shape_names(cube_glb()), [])
        raw = read_morph(morphed)
        self.assertAlmostEqual(raw[0][1], 0.5)
        moved = apply_morph([(0.0, 0.0, 0.0)], raw, 1)
        self.assertAlmostEqual(moved[0][1], 0.5)
        self.assertEqual(apply_morph([(1.0, 2.0, 3.0)], [], 1), [(1.0, 2.0, 3.0)])

        created = self.studio.project_create({"title": "Angles", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        chapter = self.studio.chapter_create({"projectId": project_id})["tree"]["chapters"][0]
        page = self.studio.page_create({"chapterId": chapter["id"], "layout": "full"})["tree"]["chapters"][0]["pages"][0]
        panel_id = page["panels"][0]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Aki"})
        character_id = made["tree"]["characters"][-1]["id"]
        model = Path(self.tmp.name) / "blink.glb"
        model.write_bytes(morphed)
        imported = self.studio.character_mesh_import({"id": character_id, "path": str(model)})
        mesh = imported["tree"]["characters"][-1]["meshes"][0]
        self.assertEqual(mesh["thumb"], "")

        image = Image.new("RGBA", (16, 16), (20, 180, 40, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        png = base64.b64encode(buffer.getvalue()).decode("ascii")
        with self.assertRaises(ValueError):
            self.studio.character_mesh_shot({"id": mesh["id"], "png": base64.b64encode(b"not a png").decode("ascii")})

        thumb = self.studio.character_mesh_thumb({"id": mesh["id"], "png": png})
        shown = thumb["tree"]["characters"][-1]["meshes"][0]
        self.assertTrue(shown["thumb"].startswith("koma:///media/"))
        self.assertTrue(shown["thumb"].endswith(".png"))
        thumb_path = self.store.get_mesh(mesh["id"])["thumb_path"]
        self.assertTrue(Path(thumb_path).is_file())
        self.assertEqual(self.store.list_refs("character", character_id), [])

        shot = self.studio.character_mesh_shot({"id": mesh["id"], "png": png, "panelId": panel_id})
        person = shot["tree"]["characters"][-1]
        pose = next(ref for ref in person["refs"] if ref["label"] == "3D angle")
        self.assertEqual(pose["kind"], "poses")
        self.assertNotEqual(person["primaryRefId"], pose["id"])
        pose_path = self.store.get_ref(pose["id"])["image_path"]
        self.assertTrue(Path(pose_path).is_file())
        replaced = self.store.get_mesh(mesh["id"])["thumb_path"]
        self.assertTrue(Path(replaced).is_file())
        self.assertFalse(Path(thumb_path).exists())
        panel = shot["tree"]["chapters"][0]["pages"][0]["panels"][0]
        self.assertEqual(panel["refs"][0]["label"], "3D angle")
        self.assertEqual(panel["refs"][0]["kind"], "reference")
        panel_path = self.store.list_refs("panel", panel_id)[0]["image_path"]
        self.assertNotEqual(panel_path, pose_path)

        other = self.studio.project_create({"title": "Other", "premise": ""})
        other_id = other["tree"]["project"]["id"]
        other_chapter = self.studio.chapter_create({"projectId": other_id})["tree"]["chapters"][0]
        other_page = self.studio.page_create({"chapterId": other_chapter["id"], "layout": "full"})
        other_panel = other_page["tree"]["chapters"][0]["pages"][0]["panels"][0]["id"]
        with self.assertRaises(ValueError) as wrong_book:
            self.studio.character_mesh_shot({"id": mesh["id"], "png": png, "panelId": other_panel})
        self.assertIn("another book", str(wrong_book.exception))

        glb_path = self.store.get_mesh(mesh["id"])["glb_path"]
        self.studio.project_delete({"id": project_id})
        self.assertFalse(Path(glb_path).exists())
        self.assertFalse(Path(replaced).exists())
        self.assertFalse(Path(pose_path).exists())
        self.assertFalse(Path(panel_path).exists())

    def test_pose_skin_names_and_saved_poses(self):
        import math

        from koma.mesh import (
            _assemble,
            _chunks,
            _invert_matrix,
            _mul_matrix,
            _node_matrix,
            cube_glb,
            humanoid_from_names,
            joint_matrix,
            skin_points,
        )

        document, blob = _chunks(cube_glb())
        document["nodes"] = [
            {"translation": [0, 2, 0], "children": [1]},
            {"mesh": 0, "translation": [0, 3, 0]},
            {"name": "J_Bip_L_UpperArm", "rotation": [0, 0, 0.70710678, 0.70710678]},
        ]
        baked_doc, _baked_blob = _chunks(_assemble(document, blob))
        # load_model_file bakes. Call the same path the importer uses.
        from koma.mesh import load_model_file

        folder = Path(self.tmp.name) / "pose"
        folder.mkdir()
        source = folder / "arm.glb"
        source.write_bytes(_assemble(document, blob))
        loaded = load_model_file(source)
        baked_doc, baked_blob = _chunks(loaded)
        self.assertNotIn("translation", baked_doc["nodes"][0])
        self.assertNotIn("translation", baked_doc["nodes"][1])
        self.assertIn("rotation", baked_doc["nodes"][2])
        self.assertGreater(min(point[1] for point in parse_positions(loaded)), 4)

        skinned = _chunks(cube_glb())[0]
        skinned["nodes"] = [
            {"name": "hips", "translation": [0, 1, 0], "rotation": [0, 0.2, 0, 0.98]},
            {"mesh": 0, "skin": 0, "translation": [0, 0.5, 0]},
        ]
        skinned["skins"] = [{"joints": [0], "skeleton": 0}]
        skinned_path = folder / "skinned.glb"
        skinned_path.write_bytes(_assemble(skinned, _chunks(cube_glb())[1]))
        kept = _chunks(load_model_file(skinned_path))[0]
        self.assertIn("rotation", kept["nodes"][0])
        self.assertEqual(kept["nodes"][1]["translation"], [0, 0.5, 0])

        mixamo = humanoid_from_names([
            (0, "mixamorig:Hips", None),
            (1, "mixamorig:Spine", 0),
            (2, "mixamorig:Spine1", 1),
            (3, "mixamorig:LeftUpLeg", 0),
            (4, "mixamorig:LeftLeg", 3),
            (5, "mixamorig:LeftFoot", 4),
            (6, "mixamorig:LeftArm", 1),
            (7, "mixamorig:LeftForeArm", 6),
            (8, "mixamorig:LeftHand", 7),
            (9, "mixamorig:RightArm", 1),
            (10, "mixamorig:RightForeArm", 9),
            (11, "mixamorig:RightHand", 10),
        ])
        self.assertEqual(mixamo["hips"], 0)
        self.assertEqual(mixamo["spine"], 1)
        self.assertEqual(mixamo["chest"], 2)
        self.assertEqual(mixamo["leftUpperLeg"], 3)
        self.assertEqual(mixamo["leftLowerLeg"], 4)
        self.assertEqual(mixamo["leftUpperArm"], 6)
        self.assertEqual(mixamo["leftLowerArm"], 7)
        self.assertEqual(mixamo["leftHand"], 8)
        self.assertEqual(mixamo["rightUpperArm"], 9)

        cesium = humanoid_from_names([
            (3, "Skeleton_torso_joint_1", None),
            (12, "Skeleton_torso_joint_2", 3),
            (13, "torso_joint_3", 12),
            (20, "Skeleton_neck_joint_1", 13),
            (21, "Skeleton_neck_joint_2", 20),
            (17, "Skeleton_arm_joint_L__4_", 13),
            (18, "Skeleton_arm_joint_L__3_", 17),
            (19, "Skeleton_arm_joint_L__2_", 18),
            (14, "Skeleton_arm_joint_R", 13),
            (15, "Skeleton_arm_joint_R__2_", 14),
            (16, "Skeleton_arm_joint_R__3_", 15),
            (8, "leg_joint_L_1", 3),
            (9, "leg_joint_L_2", 8),
            (10, "leg_joint_L_3", 9),
            (11, "leg_joint_L_5", 10),
            (4, "leg_joint_R_1", 3),
        ])
        self.assertEqual(cesium["hips"], 3)
        self.assertEqual(cesium["spine"], 12)
        self.assertEqual(cesium["chest"], 13)
        self.assertEqual(cesium["neck"], 20)
        self.assertEqual(cesium["head"], 21)
        self.assertEqual(cesium["leftUpperArm"], 17)
        self.assertEqual(cesium["leftLowerArm"], 18)
        self.assertEqual(cesium["leftHand"], 19)
        self.assertEqual(cesium["rightUpperArm"], 14)
        self.assertEqual(cesium["rightLowerArm"], 15)
        self.assertEqual(cesium["rightHand"], 16)
        self.assertEqual(cesium["leftUpperLeg"], 8)
        self.assertEqual(cesium["leftLowerLeg"], 9)
        self.assertEqual(cesium["leftFoot"], 10)
        self.assertEqual(cesium["leftToes"], 11)

        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        turn = _node_matrix({"rotation": [0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)]})
        moved = skin_points([(1.0, 0.0, 0.0)], [(0, 0, 0, 0)], [(1.0, 0.0, 0.0, 0.0)], [turn])
        self.assertAlmostEqual(moved[0][0], 0.0, places=5)
        self.assertAlmostEqual(moved[0][1], 1.0, places=5)
        mesh = _node_matrix({"translation": [4, 0, 0]})
        joint = _node_matrix({"translation": [4, 2, 0]})
        inverse_bind = _mul_matrix(_invert_matrix(joint), mesh)
        self.assertLess(max(abs(value - identity[index]) for index, value in enumerate(joint_matrix(mesh, joint, inverse_bind))), 1e-5)

        created = self.studio.project_create({"title": "Poses", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Aki"})
        character_id = made["tree"]["characters"][-1]["id"]
        with self.assertRaises(ValueError):
            self.studio.character_pose_save({"id": character_id, "name": "  ", "rotations": {}})
        with self.assertRaises(ValueError):
            self.studio.character_pose_save({"id": character_id, "name": "Wave", "rotations": {"leftHand": [0, 0, 0, 0]}})
        saved = self.studio.character_pose_save({
            "id": character_id,
            "name": "Wave",
            "shape": "Joy",
            "rotations": {"leftUpperArm": [0, 0, 0.70710678, 0.70710678]},
        })
        pose = saved["tree"]["characters"][-1]["poses"][0]
        self.assertEqual(pose["name"], "Wave")
        self.assertEqual(pose["shape"], "Joy")
        self.assertAlmostEqual(sum(value * value for value in pose["rotations"]["leftUpperArm"]), 1.0, places=5)
        again = self.studio.character_pose_save({
            "id": character_id,
            "name": "Wave",
            "shape": "",
            "rotations": {"leftUpperArm": [0, 0, 0, 1]},
        })
        poses = again["tree"]["characters"][-1]["poses"]
        self.assertEqual(len(poses), 1)
        self.assertEqual(poses[0]["id"], pose["id"])
        self.assertEqual(poses[0]["rotations"]["leftUpperArm"], [0, 0, 0, 1])
        self.studio.character_pose_delete({"id": pose["id"]})
        self.assertEqual(self.store.list_poses(character_id), [])
        self.studio.character_pose_save({
            "id": character_id,
            "name": "Sit",
            "rotations": {"leftUpperLeg": [0.2, 0, 0, 0.98]},
        })
        self.studio.character_delete({"id": character_id})
        self.assertEqual(self.store.list_poses(character_id), [])

    def test_animation_retarget_and_posed_export(self):
        import math
        import struct
        import subprocess

        from koma.mesh import (
            _assemble,
            _chunks,
            MeshError,
            animation_clips,
            bake_posed_glb,
            cube_glb,
            retarget_clips,
            sample_channel,
            sample_clip,
        )

        half = math.sin(math.pi / 4)
        turned = [0.0, 0.0, half, half]
        document, blob = _chunks(cube_glb())
        times = struct.pack("<2f", 0.0, 1.0)
        rotations = struct.pack("<8f", 0.0, 0.0, 0.0, 1.0, *turned)
        extra = times + rotations
        document["nodes"] = [
            {"name": "Hips", "translation": [0.0, 1.0, 0.0], "children": [1]},
            {"name": "LeftArm", "rotation": [0.0, 0.0, 0.0, 1.0], "children": [2]},
            {"name": "Body", "mesh": 0, "skin": 0},
        ]
        document["skins"] = [{"joints": [0, 1]}]
        document["scenes"] = [{"nodes": [0]}]
        base = len(blob)
        document["buffers"] = [{"byteLength": base + len(extra)}]
        document["bufferViews"] = list(document["bufferViews"]) + [
            {"buffer": 0, "byteOffset": base, "byteLength": 8},
            {"buffer": 0, "byteOffset": base + 8, "byteLength": 32},
        ]
        document["accessors"] = list(document["accessors"]) + [
            {"bufferView": 2, "componentType": 5126, "count": 2, "type": "SCALAR", "min": [0.0], "max": [1.0]},
            {"bufferView": 3, "componentType": 5126, "count": 2, "type": "VEC4"},
        ]
        document["animations"] = [
            {
                "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
                "samplers": [{"input": 2, "output": 3, "interpolation": "LINEAR"}],
            }
        ]
        animated = _assemble(document, blob + extra)
        clips = animation_clips(animated)
        self.assertEqual(clips[0]["name"], "Clip 1")
        self.assertEqual(clips[0]["duration"], 1.0)
        start = sample_clip(clips[0], 0.0)[1]["rotation"]
        mid = sample_clip(clips[0], 0.5)[1]["rotation"]
        end = sample_clip(clips[0], 1.0)[1]["rotation"]
        self.assertAlmostEqual(start[3], 1.0, places=5)
        self.assertAlmostEqual(mid[2], math.sin(math.pi / 8), places=5)
        self.assertAlmostEqual(mid[3], math.cos(math.pi / 8), places=5)
        self.assertAlmostEqual(end[2], half, places=5)
        self.assertAlmostEqual(sample_clip(clips[0], 1.5, loop=True)[1]["rotation"][2], mid[2], places=4)
        self.assertAlmostEqual(sample_clip(clips[0], 1.4, loop=False)[1]["rotation"][2], half, places=5)
        held = sample_channel([0.0, 1.0], [[0.0, 0.0, 0.0], [3.0, 0.0, 0.0]], 0.75, "STEP")
        self.assertEqual(held, [0.0, 0.0, 0.0])
        curved = sample_channel(
            [0.0, 1.0],
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            0.5,
            "CUBICSPLINE",
        )
        self.assertAlmostEqual(curved[0], 1.0, places=5)

        other = _chunks(cube_glb())[0]
        other["nodes"] = [
            {"name": "Root"},
            {"name": "Hips", "translation": [0.0, 2.0, 0.0], "children": [2]},
            {"name": "LeftArm", "rotation": [0.0, 0.0, 0.0, 1.0]},
        ]
        other["scenes"] = [{"nodes": [1]}]
        other_bytes = _assemble(other, _chunks(cube_glb())[1])
        moved = retarget_clips(animated, other_bytes)
        self.assertEqual(moved[0]["channels"][0]["node"], 2)
        self.assertAlmostEqual(sample_clip(moved[0], 1.0)[2]["rotation"][2], half, places=5)

        human_source = _chunks(cube_glb())[0]
        human_source["nodes"] = [
            {"name": "hips", "translation": [0.0, 1.0, 0.0], "children": [1, 2]},
            {"name": "spine", "children": [3]},
            {"name": "leftUpperLeg"},
            {"name": "chest", "children": [4, 5, 6]},
            {"name": "neck", "children": [7]},
            {"name": "leftUpperArm", "rotation": [0.0, 0.0, 0.0, 1.0]},
            {"name": "rightUpperArm"},
            {"name": "head", "translation": [0.0, 1.7, 0.0]},
        ]
        human_source["scenes"] = [{"nodes": [0]}]
        human_source["buffers"] = [{"byteLength": len(extra) + len(blob)}]
        human_source["bufferViews"] = [
            {"buffer": 0, "byteOffset": len(blob), "byteLength": 8},
            {"buffer": 0, "byteOffset": len(blob) + 8, "byteLength": 32},
        ]
        human_source["accessors"] = [
            {"bufferView": 0, "componentType": 5126, "count": 2, "type": "SCALAR"},
            {"bufferView": 1, "componentType": 5126, "count": 2, "type": "VEC4"},
        ]
        human_source["animations"] = [
            {
                "name": "Wave",
                "channels": [{"sampler": 0, "target": {"node": 5, "path": "rotation"}}],
                "samplers": [{"input": 0, "output": 1, "interpolation": "LINEAR"}],
            }
        ]
        human_target = _chunks(cube_glb())[0]
        human_target["nodes"] = [
            {"name": "mixamorig:Hips", "translation": [0.0, 1.0, 0.0], "children": [1, 2]},
            {"name": "mixamorig:Spine", "children": [3]},
            {"name": "mixamorig:LeftUpLeg"},
            {"name": "mixamorig:Spine1", "children": [4, 5, 6]},
            {"name": "mixamorig:Neck", "children": [7]},
            {"name": "mixamorig:LeftArm", "rotation": [0.0, 0.0, half, half]},
            {"name": "mixamorig:RightArm"},
            {"name": "mixamorig:Head", "translation": [0.0, 1.7, 0.0]},
        ]
        human_target["scenes"] = [{"nodes": [0]}]
        retargeted = retarget_clips(
            _assemble(human_source, blob + extra),
            _assemble(human_target, blob),
        )
        self.assertEqual(retargeted[0]["name"], "Wave")
        self.assertEqual(retargeted[0]["channels"][0]["node"], 5)
        posed = sample_clip(retargeted[0], 0.0)[5]["rotation"]
        self.assertAlmostEqual(posed[2], half, places=4)
        self.assertAlmostEqual(posed[3], half, places=4)

        with self.assertRaises(MeshError):
            retarget_clips(cube_glb(), other_bytes)
        lonely = _chunks(cube_glb())[0]
        lonely["nodes"] = [{"name": "Tail"}, {"name": "Tip"}]
        lonely["scenes"] = [{"nodes": [0]}]
        with self.assertRaises(MeshError):
            retarget_clips(animated, _assemble(lonely, blob))

        baked = bake_posed_glb(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
            [0, 1, 2],
            [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        )
        positions = parse_positions(baked)
        self.assertEqual(len(positions), 3)
        self.assertAlmostEqual(positions[2][1], 2.0, places=5)
        baked_doc, _baked_blob = _chunks(baked)
        self.assertEqual(baked_doc["nodes"][0]["name"], "Posed")
        self.assertIn("NORMAL", baked_doc["meshes"][0]["primitives"][0]["attributes"])

        created = self.studio.project_create({"title": "Clips", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Nia"})
        character_id = made["tree"]["characters"][-1]["id"]
        folder = Path(self.tmp.name) / "clips"
        folder.mkdir()
        target_path = folder / "hero.glb"
        target_path.write_bytes(other_bytes)
        clip_path = folder / "wave.glb"
        clip_path.write_bytes(animated)
        imported = self.studio.character_mesh_import({"id": character_id, "path": str(target_path)})
        mesh_id = imported["tree"]["characters"][-1]["meshes"][0]["id"]
        loaded = self.studio.character_mesh_anim({"id": mesh_id, "path": str(clip_path)})
        self.assertEqual(loaded["clips"][0]["channels"][0]["node"], 2)
        kept = self.studio.character_mesh_anims({"id": mesh_id})
        self.assertEqual(kept["clips"][0]["channels"][0]["node"], 2)
        self.assertEqual(kept["clips"][0]["name"], loaded["clips"][0]["name"])
        sidecar = Path(self.studio.store.get_mesh(mesh_id)["glb_path"] + ".anims.json")
        self.assertTrue(sidecar.is_file())
        again = self.studio.character_mesh_anim({"id": mesh_id, "path": str(clip_path)})
        self.assertEqual(len(self.studio.character_mesh_anims({"id": mesh_id})["clips"]), 1)
        self.assertEqual(again["clips"][0]["channels"][0]["node"], 2)
        with self.assertRaises(ValueError):
            self.studio.character_mesh_anim({"id": mesh_id, "path": str(folder / "missing.glb")})
        outside = Path("/etc/passwd")
        if outside.is_file():
            with self.assertRaises(ValueError):
                self.studio.character_mesh_anim({"id": mesh_id, "path": str(outside)})
        original = self.studio.character_mesh_export({"id": mesh_id, "format": "glb"})
        posed_export = self.studio.character_mesh_export_posed(
            {
                "id": mesh_id,
                "positions": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 3.0, 0.0],
                "indices": [0, 1, 2],
                "uvs": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
                "ranges": [{"start": 0, "count": 3, "color": [0.2, 0.4, 0.8], "image": -1}],
            }
        )
        self.assertTrue(posed_export["path"].endswith("-posed.glb"))
        self.assertNotEqual(Path(original["path"]).read_bytes(), Path(posed_export["path"]).read_bytes())
        self.assertAlmostEqual(parse_positions(Path(posed_export["path"]).read_bytes())[2][1], 3.0, places=5)
        stored = Path(self.studio.store.get_mesh(mesh_id)["glb_path"]).read_bytes()
        self.assertEqual(Path(original["path"]).read_bytes(), stored)
        self.studio.character_mesh_delete({"id": mesh_id})
        self.assertFalse(sidecar.is_file())

        script = r"""
const fs = require("fs");
const vm = require("vm");
global.window = global;
global.document = {
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return { getContext() { return null; } }; },
};
global.performance = { now: () => 0 };
global.requestAnimationFrame = () => 1;
vm.runInThisContext(fs.readFileSync("ui/view3d.js", "utf8"));
const mid = sampleChannel([0, 1], [[0, 0, 0, 1], [0, 0, 0.70710678118, 0.70710678118]], 0.5, "LINEAR");
const held = sampleChannel([0, 1], [[0, 0, 0], [3, 0, 0]], 0.75, "STEP");
const curved = sampleChannel([0, 1], [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [2, 0, 0], [0, 0, 0]], 0.5, "CUBICSPLINE");
process.stdout.write(JSON.stringify({ mid, held, curved }));
"""
        completed = subprocess.run(["node", "-e", script], cwd="/workspace", capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        got = json.loads(completed.stdout)
        self.assertAlmostEqual(got["mid"][2], math.sin(math.pi / 8), places=5)
        self.assertEqual(got["held"], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(got["curved"][0], 1.0, places=5)

    def test_preset_knees_bend_forward_and_hinges_clamp(self):
        import subprocess

        script = r"""
const fs = require("fs");
const vm = require("vm");
global.window = global;
global.document = {
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return { getContext() { return null; } }; },
};
global.performance = { now: () => 0 };
global.requestAnimationFrame = () => 1;
vm.runInThisContext(fs.readFileSync("ui/view3d.js", "utf8"));

function figure(rightX) {
  const nodes = [];
  const parent = [];
  const humanoid = {};
  function add(name, parentIndex, t) {
    const index = nodes.length;
    nodes.push({ t, r: [0, 0, 0, 1], s: [1, 1, 1], matrix: null });
    parent.push(parentIndex);
    if (name) humanoid[name] = index;
    return index;
  }
  const leftX = -rightX;
  const leftSign = leftX >= 0 ? 1 : -1;
  const rightSign = rightX >= 0 ? 1 : -1;
  const hips = add("hips", -1, [0, 1, 0]);
  const spine = add("spine", hips, [0, 0.15, 0]);
  const chest = add("chest", spine, [0, 0.2, 0]);
  add("neck", chest, [0, 0.2, 0]);
  add("head", humanoid.neck, [0, 0.15, 0]);
  const leftUpper = add("leftUpperArm", chest, [leftX, 0.05, 0]);
  const leftLower = add("leftLowerArm", leftUpper, [0.3 * leftSign, 0, 0]);
  const leftHand = add("leftHand", leftLower, [0.25 * leftSign, 0, 0]);
  const midProx = add("leftMiddleProximal", leftHand, [0.08 * leftSign, 0, 0]);
  const midMid = add("leftMiddleIntermediate", midProx, [0.06 * leftSign, 0, 0]);
  add("leftMiddleDistal", midMid, [0.05 * leftSign, 0, 0]);
  add(null, humanoid.leftMiddleDistal, [0.04 * leftSign, 0, 0]);
  add("leftThumbProximal", leftHand, [0.02 * leftSign, -0.03, -0.05]);
  const rightUpper = add("rightUpperArm", chest, [rightX, 0.05, 0]);
  const rightLower = add("rightLowerArm", rightUpper, [0.3 * rightSign, 0, 0]);
  add("rightHand", rightLower, [0.25 * rightSign, 0, 0]);
  const leftThigh = add("leftUpperLeg", hips, [0.1 * leftSign, -0.05, 0]);
  const leftShin = add("leftLowerLeg", leftThigh, [0, -0.4, 0]);
  add("leftFoot", leftShin, [0, -0.4, 0]);
  const rightThigh = add("rightUpperLeg", hips, [0.1 * rightSign, -0.05, 0]);
  const rightShin = add("rightLowerLeg", rightThigh, [0, -0.4, 0]);
  add("rightFoot", rightShin, [0, -0.4, 0]);
  return { rig: { nodes, parent }, humanoid };
}

function pos(geom, worlds, name) {
  const node = geom.humanoid[name];
  return [worlds[node][12], worlds[node][13], worlds[node][14]];
}
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function nrm(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}
function flexOf(geom, worlds, bindWorlds, axes, hinge) {
  const childName = (CHILD_CHAIN[hinge.bone] || []).find((item) => geom.humanoid[item] != null);
  const childDir = boneDirection(geom, worlds, hinge.bone, childName);
  let straight;
  let pole;
  if (hinge.parent) {
    straight = boneDirection(geom, worlds, hinge.parent, hinge.bone);
    pole = carriedPole(geom, bindWorlds, worlds, hinge.parent, axes, hinge.pole);
  } else {
    straight = scaleVec(axes.up, -1);
    pole = axes[hinge.pole];
  }
  return flexionDegrees(straight, childDir, pole);
}
function aimChild(geom, bone, child, target) {
  const bindWorlds = boneWorlds(geom, {});
  const node = geom.humanoid[bone];
  const dir = boneDirection(geom, bindWorlds, bone, child);
  const bindRot = quatFromMatrix(bindWorlds[node]);
  const localDir = rotateVec(invQuat(bindRot), dir);
  const parent = geom.rig.parent[node];
  const parentRot = quatFromMatrix(bindWorlds[parent]);
  const worldRot = mulQuat(parentRot, geom.rig.nodes[node].r);
  const worldDir = rotateVec(worldRot, localDir);
  const aimed = mulQuat(quatFromTo(worldDir, target), worldRot);
  return { [bone]: normalizeQuat(mulQuat(invQuat(parentRot), aimed)) };
}

const report = [];
for (const rightX of [0.2, -0.2]) {
  const geom = figure(rightX);
  const bindWorlds = boneWorlds(geom, {});
  const axes = modelAxes(geom, bindWorlds);
  const expectForward = rightX > 0 ? [0, 0, -1] : [0, 0, 1];
  const pose = buildPreset(geom, "sit");
  const worlds = boneWorlds(geom, pose);
  const hip = pos(geom, worlds, "leftUpperLeg");
  const knee = pos(geom, worlds, "leftLowerLeg");
  const ankle = pos(geom, worlds, "leftFoot");
  const thigh = nrm(sub(knee, hip));
  const shin = nrm(sub(ankle, knee));
  const sitWorlds = worlds;
  const fightWorlds = boneWorlds(geom, buildPreset(geom, "fight"));
  function limb(posed, a, b) { return nrm(sub(pos(geom, posed, b), pos(geom, posed, a))); }
  const sitUpper = limb(sitWorlds, "leftUpperArm", "leftLowerArm");
  const sitLower = limb(sitWorlds, "leftLowerArm", "leftHand");
  const fightUpper = limb(fightWorlds, "leftUpperArm", "leftLowerArm");
  const fightLower = limb(fightWorlds, "leftLowerArm", "leftHand");
  const fightLeftThigh = limb(fightWorlds, "leftUpperLeg", "leftLowerLeg");
  const fightRightThigh = limb(fightWorlds, "rightUpperLeg", "rightLowerLeg");
  const elbowFlex = Math.acos(Math.max(-1, Math.min(1, dot(fightUpper, fightLower)))) * 180 / Math.PI;
  const pointWorlds = boneWorlds(geom, buildPreset(geom, "point"));
  const pointUpper = limb(pointWorlds, "rightUpperArm", "rightLowerArm");
  const pointLower = limb(pointWorlds, "rightLowerArm", "rightHand");
  const pointElbow = Math.acos(Math.max(-1, Math.min(1, dot(pointUpper, pointLower)))) * 180 / Math.PI;
  const bindHead = quatFromMatrix(bindWorlds[geom.humanoid.head]);
  const posedHead = quatFromMatrix(pointWorlds[geom.humanoid.head]);
  const localFace = rotateVec(invQuat(bindHead), axes.forward);
  const localHeadUp = rotateVec(invQuat(bindHead), axes.up);
  const pointFace = rotateVec(posedHead, localFace);
  const pointHeadUpVec = rotateVec(posedHead, localHeadUp);
  const bindFinger = limb(bindWorlds, "leftMiddleProximal", "leftMiddleIntermediate");
  const fistFinger = limb(fightWorlds, "leftMiddleProximal", "leftMiddleIntermediate");
  const palm = palmNormal(geom, bindWorlds, "left");
  const bindHand = quatFromMatrix(bindWorlds[geom.humanoid.leftHand]);
  const fistHand = quatFromMatrix(fightWorlds[geom.humanoid.leftHand]);
  const palmLocal = rotateVec(invQuat(bindHand), palm);
  const fingerBind = dot(rotateVec(invQuat(bindHand), bindFinger), palmLocal);
  const fingerFist = dot(rotateVec(invQuat(fistHand), fistFinger), palmLocal);
  const hinges = {};
  const waveWorlds = boneWorlds(geom, buildPreset(geom, "wave"));
  const waveUpper = limb(waveWorlds, "rightUpperArm", "rightLowerArm");
  const waveLower = limb(waveWorlds, "rightLowerArm", "rightHand");
  const waveLeft = limb(waveWorlds, "leftUpperArm", "leftLowerArm");
  const waveElbow = Math.acos(Math.max(-1, Math.min(1, dot(waveUpper, waveLower)))) * 180 / Math.PI;
  const waveHead = quatFromMatrix(waveWorlds[geom.humanoid.head]);
  const kneelWorlds = boneWorlds(geom, buildPreset(geom, "kneel"));
  const kneelRightThigh = limb(kneelWorlds, "rightUpperLeg", "rightLowerLeg");
  const kneelRightShin = limb(kneelWorlds, "rightLowerLeg", "rightFoot");
  const kneelLeftShin = limb(kneelWorlds, "leftLowerLeg", "leftFoot");
  const kneelLeftThigh = limb(kneelWorlds, "leftUpperLeg", "leftLowerLeg");
  const kneelHead = quatFromMatrix(kneelWorlds[geom.humanoid.head]);
  const strideWorlds = boneWorlds(geom, buildPreset(geom, "stride"));
  const strideLead = limb(strideWorlds, "leftUpperLeg", "leftLowerLeg");
  const strideTrail = limb(strideWorlds, "rightUpperLeg", "rightLowerLeg");
  const strideArmLead = limb(strideWorlds, "rightUpperArm", "rightLowerArm");
  const strideArmBack = limb(strideWorlds, "leftUpperArm", "leftLowerArm");
  const strideSpine = limb(strideWorlds, "spine", "chest");
  const strideHead = quatFromMatrix(strideWorlds[geom.humanoid.head]);
  const crouchWorlds = boneWorlds(geom, buildPreset(geom, "crouch"));
  const crouchThigh = limb(crouchWorlds, "leftUpperLeg", "leftLowerLeg");
  const crouchShin = limb(crouchWorlds, "leftLowerLeg", "leftFoot");
  const crouchHead = quatFromMatrix(crouchWorlds[geom.humanoid.head]);
  const wavePose = buildPreset(geom, "wave");
  for (const name of ["relaxed", "walk", "run", "sit", "point", "cross", "fight", "wave", "kneel", "stride", "crouch"]) {
    const posed = boneWorlds(geom, buildPreset(geom, name));
    hinges[name] = POSE_HINGES.map((hinge) => flexOf(geom, posed, bindWorlds, axes, hinge));
  }
  const hyper = aimChild(geom, "leftLowerLeg", "leftFoot", axes.forward);
  const before = nrm(sub(pos(geom, boneWorlds(geom, hyper), "leftFoot"), pos(geom, boneWorlds(geom, hyper), "leftLowerLeg")));
  clampHinges(geom, hyper);
  const afterWorlds = boneWorlds(geom, hyper);
  const after = nrm(sub(pos(geom, afterWorlds, "leftFoot"), pos(geom, afterWorlds, "leftLowerLeg")));
  const back = scaleVec(axes.forward, -1);
  const folded = nrm([back[0] * 0.65 - axes.up[0] * 0.75, back[1] * 0.65 - axes.up[1] * 0.75, back[2] * 0.65 - axes.up[2] * 0.75]);
  const legal = aimChild(geom, "leftLowerLeg", "leftFoot", folded);
  const legalFlex = flexOf(geom, boneWorlds(geom, legal), bindWorlds, axes, POSE_HINGES[2]);
  clampHinges(geom, legal);
  const keptFlex = flexOf(geom, boneWorlds(geom, legal), bindWorlds, axes, POSE_HINGES[2]);
  report.push({
    rightX,
    forward: axes.forward,
    expectForward,
    thighForward: dot(thigh, axes.forward),
    shinUp: dot(shin, axes.up),
    kneeFlex: flexOf(geom, worlds, bindWorlds, axes, POSE_HINGES[2]),
    sitUpperUp: dot(sitUpper, axes.up),
    sitLowerUp: dot(sitLower, axes.up),
    sitLowerForward: dot(sitLower, axes.forward),
    fightUpperUp: dot(fightUpper, axes.up),
    fightLowerUp: dot(fightLower, axes.up),
    fightElbow: elbowFlex,
    fightLead: dot(fightLeftThigh, axes.forward),
    fightBack: dot(fightRightThigh, axes.forward),
    fightLowerForward: dot(fightLower, axes.forward),
    pointUpperUp: dot(pointUpper, axes.up),
    pointLowerUp: dot(pointLower, axes.up),
    pointLowerForward: dot(pointLower, axes.forward),
    pointLowerRight: dot(pointLower, axes.right),
    pointElbow,
    pointHeadUp: dot(pointHeadUpVec, axes.up),
    pointFaceForward: dot(pointFace, axes.forward),
    pointFaceRight: dot(pointFace, axes.right),
    fingerBind,
    fingerFist,
    waveUpperUp: dot(waveUpper, axes.up),
    waveLowerUp: dot(waveLower, axes.up),
    waveLowerRight: dot(waveLower, axes.right),
    waveLeftUp: dot(waveLeft, axes.up),
    waveElbow,
    waveHeadUp: dot(rotateVec(waveHead, localHeadUp), axes.up),
    waveFaceForward: dot(rotateVec(waveHead, localFace), axes.forward),
    waveFingers: wavePose.leftMiddleProximal ? 1 : 0,
    kneelRightForward: dot(kneelRightThigh, axes.forward),
    kneelRightUp: dot(kneelRightThigh, axes.up),
    kneelRightShinForward: dot(kneelRightShin, axes.forward),
    kneelLeftShinUp: dot(kneelLeftShin, axes.up),
    kneelLeftForward: dot(kneelLeftThigh, axes.forward),
    kneelHeadUp: dot(rotateVec(kneelHead, localHeadUp), axes.up),
    kneelKnee: flexOf(geom, kneelWorlds, bindWorlds, axes, POSE_HINGES[3]),
    strideLead: dot(strideLead, axes.forward),
    strideTrail: dot(strideTrail, axes.forward),
    strideArmLead: dot(strideArmLead, axes.forward),
    strideArmBack: dot(strideArmBack, axes.forward),
    strideSpineUp: dot(strideSpine, axes.up),
    strideHeadUp: dot(rotateVec(strideHead, localHeadUp), axes.up),
    crouchThighForward: dot(crouchThigh, axes.forward),
    crouchThighUp: dot(crouchThigh, axes.up),
    crouchShinUp: dot(crouchShin, axes.up),
    crouchHeadUp: dot(rotateVec(crouchHead, localHeadUp), axes.up),
    crouchKnee: flexOf(geom, crouchWorlds, bindWorlds, axes, POSE_HINGES[2]),
    hyperBefore: dot(before, axes.forward),
    hyperAfter: dot(after, axes.forward),
    legalFlex,
    keptFlex,
    hinges,
  });
}
process.stdout.write(JSON.stringify(report));
"""
        completed = subprocess.run(["node", "-e", script], cwd="/workspace", capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(len(report), 2)
        for item in report:
            self.assertGreater(sum(a * b for a, b in zip(item["forward"], item["expectForward"])), 0.9)
            self.assertGreater(item["thighForward"], 0.7)
            self.assertLess(item["shinUp"], -0.7)
            self.assertGreater(item["kneeFlex"], 40)
            self.assertLess(item["sitUpperUp"], -0.7)
            self.assertLess(item["sitLowerUp"], -0.5)
            self.assertGreater(item["sitLowerForward"], 0.3)
            self.assertLess(item["fightUpperUp"], 0.15)
            self.assertLess(item["fightLowerUp"], 0.35)
            self.assertGreater(item["fightLowerForward"], 0.5)
            self.assertGreater(item["fightElbow"], 70)
            self.assertLess(abs(item["pointUpperUp"]), 0.35)
            self.assertLess(abs(item["pointLowerUp"]), 0.35)
            self.assertGreater(item["pointLowerForward"], 0.75)
            self.assertGreater(item["pointLowerRight"], 0.2)
            self.assertLess(item["pointLowerRight"], 0.55)
            self.assertLess(item["pointElbow"], 20)
            self.assertGreater(item["pointHeadUp"], 0.9)
            self.assertGreater(item["pointFaceForward"], 0.75)
            self.assertGreater(item["pointFaceRight"], 0.2)
            self.assertGreater(item["fingerFist"], item["fingerBind"] + 0.35)
            self.assertGreater(item["waveUpperUp"], 0.45)
            self.assertGreater(item["waveLowerUp"], 0.45)
            self.assertGreater(item["waveLowerRight"], 0)
            self.assertGreater(item["waveElbow"], 45)
            self.assertLess(item["waveLeftUp"], -0.7)
            self.assertGreater(item["waveHeadUp"], 0.9)
            self.assertGreater(item["waveFaceForward"], 0.9)
            self.assertEqual(item["waveFingers"], 0)
            self.assertGreater(item["kneelRightForward"], 0.15)
            self.assertLess(item["kneelRightUp"], -0.8)
            self.assertLess(item["kneelRightShinForward"], -0.7)
            self.assertLess(item["kneelLeftShinUp"], -0.5)
            self.assertGreater(item["kneelLeftForward"], 0.3)
            self.assertGreater(item["kneelHeadUp"], 0.9)
            self.assertGreater(item["kneelKnee"], 50)
            self.assertGreater(item["strideLead"], 0.6)
            self.assertLess(item["strideTrail"], -0.5)
            self.assertGreater(item["strideArmLead"], 0.5)
            self.assertLess(item["strideArmBack"], -0.4)
            self.assertGreater(item["strideSpineUp"], 0.85)
            self.assertGreater(item["strideHeadUp"], 0.9)
            self.assertGreater(item["crouchThighForward"], 0.5)
            self.assertLess(item["crouchThighUp"], -0.4)
            self.assertLess(item["crouchShinUp"], -0.7)
            self.assertGreater(item["crouchHeadUp"], 0.9)
            self.assertGreater(item["crouchKnee"], 40)
            self.assertGreater(item["fightLead"], 0.5)
            self.assertLess(item["fightBack"], 0)
            self.assertGreater(item["hyperBefore"], 0.7)
            self.assertLess(item["hyperAfter"], 0.15)
            self.assertGreater(item["legalFlex"], 30)
            self.assertGreater(item["keptFlex"], 30)
            for name, angles in item["hinges"].items():
                self.assertEqual(len(angles), 6)
                for angle, hinge in zip(angles, (
                    (-65, 115), (-65, 115), (0, 150), (0, 150), (0, 150), (0, 150),
                )):
                    self.assertGreaterEqual(angle, hinge[0] - 0.5, name)
                    self.assertLessEqual(angle, hinge[1] + 0.5, name)

    def test_finger_curl_closes_the_hand(self):
        import subprocess

        script = r"""
const fs = require("fs");
const vm = require("vm");
global.window = global;
global.document = {
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return { getContext() { return null; } }; },
};
global.performance = { now: () => 0 };
global.requestAnimationFrame = () => 1;
vm.runInThisContext(fs.readFileSync("ui/view3d.js", "utf8"));

function figure() {
  const nodes = [];
  const parent = [];
  const humanoid = {};
  function add(name, parentIndex, t) {
    const index = nodes.length;
    nodes.push({ t, r: [0, 0, 0, 1], s: [1, 1, 1], matrix: null });
    parent.push(parentIndex);
    if (name) humanoid[name] = index;
    return index;
  }
  const hips = add("hips", -1, [0, 1, 0]);
  const spine = add("spine", hips, [0, 0.15, 0]);
  const chest = add("chest", spine, [0, 0.2, 0]);
  const hand = add("leftHand", chest, [-0.2, 0.05, 0]);
  const mid = add("leftMiddleProximal", hand, [-0.08, 0, 0]);
  const mid2 = add("leftMiddleIntermediate", mid, [-0.06, 0, 0]);
  add("leftMiddleDistal", mid2, [-0.05, 0, 0]);
  add(null, humanoid.leftMiddleDistal, [-0.04, 0, 0]);
  add("leftThumbProximal", hand, [-0.02, -0.03, -0.05]);
  return { rig: { nodes, parent }, humanoid };
}
function nrm(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}
function fingerDot(pose) {
  const geom = figure();
  const bindWorlds = boneWorlds(geom, {});
  const worlds = boneWorlds(geom, pose);
  const a = geom.humanoid.leftMiddleProximal;
  const b = geom.humanoid.leftMiddleIntermediate;
  const dir = nrm([
    worlds[b][12] - worlds[a][12],
    worlds[b][13] - worlds[a][13],
    worlds[b][14] - worlds[a][14],
  ]);
  const palm = palmNormal(geom, bindWorlds, "left");
  const hand = quatFromMatrix(worlds[geom.humanoid.leftHand]);
  return dot(rotateVec(invQuat(hand), dir), rotateVec(invQuat(quatFromMatrix(bindWorlds[geom.humanoid.leftHand])), palm));
}
const geom = figure();
const bindWorlds = boneWorlds(geom, {});
const half = {};
const full = {};
curlFingers(geom, half, bindWorlds, 0.45);
curlFingers(geom, full, bindWorlds, 1);
const empty = {};
curlFingers(geom, empty, bindWorlds, 0);
process.stdout.write(JSON.stringify({
  open: fingerDot({}),
  half: fingerDot(half),
  full: fingerDot(full),
  emptyKeys: Object.keys(empty).length,
  halfKeys: Object.keys(half).length,
}));
"""
        completed = subprocess.run(["node", "-e", script], cwd="/workspace", capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["emptyKeys"], 0)
        self.assertGreater(report["halfKeys"], 0)
        self.assertGreater(report["half"], report["open"] + 0.15)
        self.assertGreater(report["full"], report["half"] + 0.15)

    def test_mirror_copies_left_limbs_to_the_right(self):
        import subprocess

        script = r"""
const fs = require("fs");
const vm = require("vm");
global.window = global;
global.document = {
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return { getContext() { return null; } }; },
};
global.performance = { now: () => 0 };
global.requestAnimationFrame = () => 1;
vm.runInThisContext(fs.readFileSync("ui/view3d.js", "utf8"));

function figure() {
  const nodes = [];
  const parent = [];
  const humanoid = {};
  function add(name, parentIndex, t) {
    const index = nodes.length;
    nodes.push({ t, r: [0, 0, 0, 1], s: [1, 1, 1], matrix: null });
    parent.push(parentIndex);
    if (name) humanoid[name] = index;
    return index;
  }
  const hips = add("hips", -1, [0, 1, 0]);
  const spine = add("spine", hips, [0, 0.15, 0]);
  const chest = add("chest", spine, [0, 0.2, 0]);
  add("neck", chest, [0, 0.2, 0]);
  add("head", humanoid.neck, [0, 0.15, 0]);
  const leftUpper = add("leftUpperArm", chest, [-0.2, 0.05, 0]);
  const leftLower = add("leftLowerArm", leftUpper, [-0.3, 0, 0]);
  add("leftHand", leftLower, [-0.25, 0, 0]);
  const rightUpper = add("rightUpperArm", chest, [0.2, 0.05, 0]);
  const rightLower = add("rightLowerArm", rightUpper, [0.3, 0, 0]);
  add("rightHand", rightLower, [0.25, 0, 0]);
  const leftThigh = add("leftUpperLeg", hips, [-0.1, -0.05, 0]);
  const leftShin = add("leftLowerLeg", leftThigh, [0, -0.4, 0]);
  add("leftFoot", leftShin, [0, -0.4, 0]);
  const rightThigh = add("rightUpperLeg", hips, [0.1, -0.05, 0]);
  const rightShin = add("rightLowerLeg", rightThigh, [0, -0.4, 0]);
  add("rightFoot", rightShin, [0, -0.4, 0]);
  const mid = add("leftMiddleProximal", humanoid.leftHand, [-0.08, 0, 0]);
  add("leftMiddleIntermediate", mid, [-0.06, 0, 0]);
  add("rightMiddleProximal", humanoid.rightHand, [0.08, 0, 0]);
  add("rightMiddleIntermediate", humanoid.rightMiddleProximal, [0.06, 0, 0]);
  return { rig: { nodes, parent }, humanoid };
}

function pos(geom, worlds, name) {
  const node = geom.humanoid[name];
  return [worlds[node][12], worlds[node][13], worlds[node][14]];
}
function nrm(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}
function limb(geom, worlds, parentName, childName) {
  return nrm(subVec(pos(geom, worlds, childName), pos(geom, worlds, parentName)));
}
function dots(v, axes) {
  return [dot(v, axes.forward), dot(v, axes.up), dot(v, axes.right)];
}

const geom = figure();
const bindWorlds = boneWorlds(geom, {});
const axes = modelAxes(geom, bindWorlds);
const step = {};
aimBone(geom, step, bindWorlds, "leftUpperArm", mix(axes, [["forward", 0.85], ["up", 0.35], ["right", -0.4]]));
aimBone(geom, step, boneWorlds(geom, step), "leftLowerArm", mix(axes, [["forward", 0.2], ["up", -0.2], ["right", -0.95]]));
aimBone(geom, step, boneWorlds(geom, step), "leftUpperLeg", mix(axes, [["forward", 0.75], ["up", -0.65]]));
const pose = mirrorPoseLeftToRight(geom, step);
const worlds = boneWorlds(geom, pose);
const pairs = [
  ["leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm"],
  ["leftLowerArm", "leftHand", "rightLowerArm", "rightHand"],
  ["leftUpperLeg", "leftLowerLeg", "rightUpperLeg", "rightLowerLeg"],
];
const report = pairs.map((names) => {
  const left = dots(limb(geom, worlds, names[0], names[1]), axes);
  const right = dots(limb(geom, worlds, names[2], names[3]), axes);
  return { left, right };
});
const rest = mirrorPoseLeftToRight(geom, {});
const restWorlds = boneWorlds(geom, rest);
const restRight = limb(geom, restWorlds, "rightUpperArm", "rightLowerArm");
const bindRight = limb(geom, bindWorlds, "rightUpperArm", "rightLowerArm");
process.stdout.write(JSON.stringify({ report, restRight, bindRight, rightKeys: Object.keys(pose).filter((name) => name.startsWith("right")) }));
"""
        completed = subprocess.run(["node", "-e", script], cwd="/workspace", capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        got = json.loads(completed.stdout)
        self.assertIn("rightUpperArm", got["rightKeys"])
        self.assertIn("rightUpperLeg", got["rightKeys"])
        for item in got["report"]:
            self.assertAlmostEqual(item["left"][0], item["right"][0], delta=0.08)
            self.assertAlmostEqual(item["left"][1], item["right"][1], delta=0.08)
            self.assertAlmostEqual(item["left"][2], -item["right"][2], delta=0.08)
        for left, right in zip(got["bindRight"], got["restRight"]):
            self.assertAlmostEqual(left, right, delta=0.05)

    def test_long_sound_effect_is_not_cut_off(self):
        art = Path(self.tmp.name) / "art.png"
        Image.new("RGB", (80, 120), (240, 240, 240)).save(art)
        words = "KRAKOOM " * 6
        full = Path(self.tmp.name) / "full-sfx.png"
        cut = Path(self.tmp.name) / "cut-sfx.png"
        panel = {
            "slot": 0,
            "image_path": str(art),
            "dialogue": "",
            "balloon": "none",
            "balloon_anchor": "bl",
            "sfx": words,
        }
        render_page("full", "ltr", [panel], "1-1", full, 600, 900)
        panel["sfx"] = words[:24]
        render_page("full", "ltr", [panel], "1-1", cut, 600, 900)
        full_hash = hash(Image.open(full).tobytes())
        cut_hash = hash(Image.open(cut).tobytes())
        self.assertNotEqual(full_hash, cut_hash, "long sound effect matched its 24-character prefix")
        self.assertLess(full.stat().st_size, 300_000)

    def test_turntable_export_writes_a_gif_and_png_sequence(self) -> None:
        import base64
        import io
        from pathlib import Path

        from PIL import Image

        created = self.studio.project_create({"title": "Turn", "premise": ""})
        project_id = created["tree"]["project"]["id"]
        made = self.studio.character_create({"projectId": project_id, "name": "Aki"})
        character_id = made["tree"]["characters"][-1]["id"]
        from koma.mesh import cube_glb

        glb_path = Path(self.tmp.name) / "sample.glb"
        glb_path.write_bytes(cube_glb())
        imported = self.studio.character_mesh_import({"id": character_id, "path": str(glb_path)})
        mesh_id = imported["tree"]["characters"][-1]["meshes"][0]["id"]
        frames = []
        for index in range(8):
            size = (1000, 800) if index == 0 else (64, 48)
            image = Image.new("RGB", size, (20 + index * 20, 40, 80))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            frames.append("data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))
        gif = self.studio.character_mesh_turntable({"id": mesh_id, "kind": "gif", "frames": frames})
        gif_path = Path(gif["path"])
        self.assertTrue(gif_path.is_file())
        self.assertEqual(gif_path.suffix, ".gif")
        self.assertEqual(gif["frames"], 8)
        with Image.open(gif_path) as movie:
            self.assertEqual(movie.format, "GIF")
            self.assertEqual(movie.n_frames, 8)
            self.assertLessEqual(max(movie.size), 480)
        png = self.studio.character_mesh_turntable({"id": mesh_id, "kind": "png", "frames": frames})
        folder = Path(png["path"])
        names = sorted(path.name for path in folder.glob("frame-*.png"))
        self.assertEqual(names, [f"frame-{index:02d}.png" for index in range(1, 9)])
        with Image.open(folder / "frame-01.png") as first:
            self.assertEqual(first.format, "PNG")
            self.assertLessEqual(max(first.size), 480)
        short = frames[:3]
        with self.assertRaises(ValueError):
            self.studio.character_mesh_turntable({"id": mesh_id, "kind": "gif", "frames": short})
        app = (Path(__file__).resolve().parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-act="mesh-turntable"', app)
        self.assertIn("turntableFrames", app)
        self.assertIn('"character.mesh_turntable"', (Path(__file__).resolve().parents[1] / "koma" / "service.py").read_text(encoding="utf-8"))

    def test_viewer_toggles_keep_the_model_and_theme_is_shared(self) -> None:
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        app = (root / "ui" / "app.js").read_text(encoding="utf-8")
        css = (root / "ui" / "app.css").read_text(encoding="utf-8")
        self.assertIn('data-act="theme"', app)
        self.assertIn("koma-theme", app)
        self.assertIn('html[data-theme="light"]', css)
        self.assertIn("--chip:", css)
        for name in ("mesh-auto", "mesh-wire", "mesh-light", "mesh-grid", "mesh-outline", "mesh-ground", "mesh-angle"):
            match = re.search(rf'"{name}"\([^)]*\)\s*\{{(.*?)\n  \}},', app, re.S)
            self.assertIsNotNone(match, name)
            body = match.group(1)
            self.assertIn("paintMeshChrome()", body, name)
            self.assertNotIn("render()", body, name)

    def test_outline_toggle_starts_off_and_is_in_the_viewer(self) -> None:
        from pathlib import Path

        app = (Path(__file__).resolve().parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
        view = (Path(__file__).resolve().parents[1] / "ui" / "view3d.js").read_text(encoding="utf-8")
        self.assertIn('data-act="mesh-outline"', app)
        self.assertIn('"mesh-outline"', app)
        self.assertIn("outline: false", view)
        self.assertIn("drawOutlineGl", view)
        self.assertIn("paintCanvasOutline", view)
        self.assertIn("uOutline", view)
        self.assertIn("gl.FRONT", view)

    def fail_transport(self, method, url, headers, body):
        raise AssertionError("provider should not call the network")


def parse_positions(data: bytes):
    from koma.mesh import parse_glb

    positions, _indices = parse_glb(data)
    return positions


def render_silent(art: Path, target: Path) -> bytes:
    render_page(
        "full",
        "ltr",
        [{"slot": 0, "image_path": str(art), "dialogue": "", "balloon": "none", "balloon_anchor": "tr", "sfx": ""}],
        "1-2",
        target,
    )
    return Image.open(target).tobytes()


if __name__ == "__main__":
    unittest.main()
