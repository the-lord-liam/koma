"""Native window. GTK hosts the studio UI and talks to it over a local bridge."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import traceback
from pathlib import Path
from urllib.parse import unquote

from koma.ai import AIError
from koma.paths import ROOT, UI_DIR, images_dir, previews_dir
from koma.service import Studio

UI_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}
MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def main() -> None:
    install_launcher()
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import GLib, Gio, Gtk, WebKit2

    GLib.set_prgname("koma")
    GLib.set_application_name("Koma")
    window = Window(Gtk, WebKit2, Gio, GLib)
    window.run()
    if window.exit_code:
        sys.exit(window.exit_code)


class Window:
    def __init__(self, Gtk, WebKit2, Gio, GLib):
        self.Gtk = Gtk
        self.WebKit2 = WebKit2
        self.GLib = GLib
        self.Gio = Gio
        self.smoke = os.environ.get("KOMA_SMOKE") == "1"
        self.exit_code = 0
        self._smoke_done = False
        self._smoke_tries = 0
        self._kept: list = []
        self.jobs: queue.Queue = queue.Queue()
        from koma.paths import db_path
        from koma.store import Store

        self.studio = Studio(Store(db_path()), status=self._status)
        self._worker = __import__("threading").Thread(target=self._work, daemon=True)
        self._worker.start()

        context = WebKit2.WebContext.get_default()
        security = context.get_security_manager()
        security.register_uri_scheme_as_secure("koma")
        security.register_uri_scheme_as_local("koma")
        security.register_uri_scheme_as_cors_enabled("koma")
        context.register_uri_scheme("koma", self._on_uri)

        manager = WebKit2.UserContentManager()
        manager.register_script_message_handler("koma")
        manager.connect("script-message-received::koma", self._on_script)
        self.web = WebKit2.WebView.new_with_user_content_manager(manager)
        settings = WebKit2.Settings()
        settings.set_enable_javascript(True)
        settings.set_enable_write_console_messages_to_stdout(True)
        settings.set_allow_file_access_from_file_urls(True)
        if hasattr(settings, "set_enable_back_forward_navigation_gestures"):
            settings.set_enable_back_forward_navigation_gestures(False)
        self.web.set_settings(settings)
        self.web.set_zoom_level(1.0)
        self.web.connect("load-failed", self._on_load_failed)

        self.win = Gtk.Window(title="Koma")
        self._place_window()
        icon = Path.home() / ".local/share/icons/hicolor/256x256/apps/koma.png"
        if icon.exists():
            self.win.set_icon_from_file(str(icon))
        self.win.add(self.web)
        self.win.connect("destroy", self._on_destroy)
        if self.smoke:
            self.web.connect("load-changed", self._on_load_changed)
            GLib.timeout_add(12000, self._smoke_timeout)

    def _place_window(self) -> None:
        """Size the window to the monitor work area so the desk is not clipped."""
        self.win.set_resizable(True)
        try:
            self.win.set_size_request(720, 480)
        except Exception:
            pass
        if self.smoke:
            self.win.set_default_size(1280, 800)
            return
        width, height = 1280, 800
        try:
            screen = self.win.get_screen()
            monitor = screen.get_primary_monitor()
            area = screen.get_monitor_workarea(monitor)
            width = max(960, int(area.width * 0.95))
            height = max(640, int(area.height * 0.95))
        except Exception:
            width, height = 1280, 800
        self.win.set_default_size(width, height)

    def run(self) -> None:
        self.win.show_all()
        self.web.load_uri("koma:///ui/index.html")
        self.Gtk.main()

    def _on_destroy(self, *_args):
        if self.smoke and not self._smoke_done:
            self.exit_code = 1
        self.Gtk.main_quit()

    def _on_load_failed(self, _web, _event, uri, error):
        print(f"Koma failed to load {uri}: {error}", file=sys.stderr)
        return False

    def _on_load_changed(self, _web, event):
        if event == self.WebKit2.LoadEvent.FINISHED:
            self.GLib.timeout_add(300, self._poll_smoke)

    def _poll_smoke(self):
        self._smoke_tries += 1
        self.web.run_javascript(
            "(function(){var m=document.getElementById('main');"
            "return (m&&m.dataset.ready||'missing')+'|'+(m&&m.dataset.error||'')"
            "+'|boards='+document.querySelectorAll('.board-face').length;})()",
            None,
            self._smoke_result,
            None,
        )
        return False

    def _smoke_result(self, web, result, _data):
        if self._smoke_done:
            return
        try:
            value = web.run_javascript_finish(result)
            text = value.get_js_value().to_string()
        except Exception as error:
            text = f"js-error|{error}"
        if text.startswith("1") or text.startswith("error") or self._smoke_tries >= 20:
            self._finish_smoke(text)
        else:
            self.GLib.timeout_add(400, self._poll_smoke)

    def _smoke_timeout(self):
        if not self._smoke_done:
            self._finish_smoke("timeout")
        return False

    def _finish_smoke(self, text: str) -> None:
        if self._smoke_done:
            return
        self._smoke_done = True
        print(f"SMOKE {text}", flush=True)
        self.exit_code = 0 if text.startswith("1") else 1
        self.Gtk.main_quit()

    def _on_uri(self, request, *_args):
        try:
            path = unquote(request.get_path() or "")
            parts = [part for part in path.split("/") if part]
            if len(parts) == 2 and parts[0] == "ui" and parts[1] in UI_FILES:
                data = (UI_DIR / parts[1]).read_bytes()
                mime = UI_FILES[parts[1]]
            elif len(parts) == 2 and parts[0] == "media" and _safe_media(parts[1]):
                data = (images_dir() / parts[1]).read_bytes()
                mime = MEDIA_TYPES[parts[1].rsplit(".", 1)[-1]]
            elif len(parts) == 2 and parts[0] == "preview" and _safe_media(parts[1]) and parts[1].endswith(".png"):
                data = (previews_dir() / parts[1]).read_bytes()
                mime = "image/png"
            else:
                raise FileNotFoundError(path)
        except Exception as error:
            data = f"Not found: {error}".encode()
            mime = "text/plain; charset=utf-8"
        self._finish(request, data, mime)

    def _finish(self, request, data: bytes, mime: str) -> None:
        gbytes = self.GLib.Bytes.new(data)
        stream = self.Gio.MemoryInputStream.new_from_bytes(gbytes)
        self._kept.append(gbytes)
        if len(self._kept) > 48:
            self._kept = self._kept[-24:]
        request.finish(stream, len(data), mime)

    def _on_script(self, _manager, result):
        try:
            payload = _decode_message(result.get_js_value())
        except Exception:
            traceback.print_exc()
            return
        if not isinstance(payload, dict) or "id" not in payload or "method" not in payload:
            return
        if payload["method"] == "shell.pick_image":
            self._pick_image(payload)
            return
        if payload["method"] == "shell.set_title":
            self._set_title(payload)
            return
        if payload["method"] == "shell.open_url":
            self._open_url(payload)
            return
        self.jobs.put(payload)

    def _set_title(self, payload: dict) -> None:
        raw = str((payload.get("params") or {}).get("title") or "")
        title = " ".join(raw.split())
        if not title.startswith("Koma") or len(title) > 160:
            title = "Koma"
        self.win.set_title(title)
        self._push({"id": payload["id"], "ok": True, "result": {"ok": True}})

    def _open_url(self, payload: dict) -> None:
        url = str((payload.get("params") or {}).get("url") or "")
        if url.rstrip("/") != "https://x.com/the_lord_liam":
            self._push({"id": payload["id"], "ok": False, "error": "That link is not the studio credit."})
            return
        try:
            subprocess.Popen(["xdg-open", "https://x.com/the_lord_liam"])
        except OSError as error:
            self._push({"id": payload["id"], "ok": False, "error": str(error)})
            return
        self._push({"id": payload["id"], "ok": True, "result": {"ok": True}})

    def _pick_image(self, payload: dict) -> None:
        try:
            dialog = self.Gtk.FileChooserNative.new(
                "Choose a reference image",
                self.win,
                self.Gtk.FileChooserAction.OPEN,
                "Open",
                "Cancel",
            )
            image_filter = self.Gtk.FileFilter()
            image_filter.set_name("Images")
            image_filter.add_mime_type("image/png")
            image_filter.add_mime_type("image/jpeg")
            image_filter.add_mime_type("image/webp")
            dialog.add_filter(image_filter)
            path = ""
            if dialog.run() == self.Gtk.ResponseType.ACCEPT:
                path = dialog.get_filename() or ""
            dialog.destroy()
            self._push({"id": payload["id"], "ok": True, "result": {"path": path}})
        except Exception as error:
            traceback.print_exc()
            self._push({"id": payload["id"], "ok": False, "error": str(error)})

    def _work(self) -> None:
        while True:
            payload = self.jobs.get()
            try:
                result = self.studio.dispatch(payload["method"], payload.get("params") or {})
                self._push({"id": payload["id"], "ok": True, "result": result})
            except (AIError, KeyError, ValueError) as error:
                self._push({"id": payload["id"], "ok": False, "error": str(error)})
            except Exception:
                traceback.print_exc()
                self._push(
                    {
                        "id": payload["id"],
                        "ok": False,
                        "error": "Something broke while handling that. Details are in the terminal.",
                    }
                )

    def _status(self, text: str) -> None:
        self._push({"type": "event", "event": "status", "text": text})

    def _push(self, message: dict) -> None:
        self.GLib.idle_add(self._deliver, message)

    def _deliver(self, message: dict):
        blob = json.dumps(message).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        self.web.run_javascript(f"window.koma && window.koma._recv({blob})", None, None, None)
        return False


def _safe_media(name: str) -> bool:
    stem, dot, ext = name.partition(".")
    return (
        dot == "."
        and ext in MEDIA_TYPES
        and len(stem) == 32
        and all(char in "0123456789abcdef" for char in stem)
    )


def _decode_message(value):
    text = value.to_string()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = json.loads(value.to_json(0))
    if isinstance(data, str):
        data = json.loads(data)
    return data


def install_launcher() -> None:
    try:
        icon = _write_icon()
        apps = Path.home() / ".local" / "share" / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        launcher = ROOT / "bin" / "koma"
        entry = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Koma\n"
            "GenericName=Manga Studio\n"
            "Comment=Plan, draw, and assemble manga\n"
            f"Exec={launcher}\n"
            f"Icon={icon}\n"
            "Terminal=false\n"
            "Categories=Graphics;Art;\n"
            "Keywords=manga;comic;draw;\n"
            "StartupNotify=true\n"
        )
        (apps / "koma.desktop").write_text(entry, encoding="utf-8")
    except Exception as error:
        print(f"Could not install the app launcher: {error}", file=sys.stderr)


def _write_icon() -> Path:
    from PIL import Image, ImageDraw

    from koma.compose import font

    folder = Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "koma.png"
    image = Image.new("RGBA", (256, 256), (20, 18, 16, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, 238, 238), radius=52, fill=(226, 59, 43, 255))
    draw.text((128, 134), "コマ", font=font(108, bold=True), fill="white", anchor="mm")
    image.save(target)
    return target
