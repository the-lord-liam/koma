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
