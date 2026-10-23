#!/usr/bin/env python3
"""Capture real desktop and mobile screenshots from the shipped static UI.

Run after installing Playwright and its Chromium browser. No backend or NAS
connection is needed: the diagnostic API response is locally stubbed only.
"""
from __future__ import annotations
import http.server
import json
import threading
import shutil
import socketserver
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
DOCS = ROOT / "docs"


class Site(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/info":
            data = json.dumps({"version": "1.7.0", "max_batch": 12,
                               "max_upload_mb": 15, "engine_available": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        filename = "index.html" if self.path == "/" else self.path.removeprefix("/static/")
        if filename not in ("index.html", "app.css", "app.js", "mark.svg"):
            self.send_error(404)
            return
        target = STATIC / filename
        content = target.read_bytes()
        mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                ".svg": "image/svg+xml"}[target.suffix]
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    DOCS.mkdir(exist_ok=True)
    server = ThreadingServer(("127.0.0.1", 0), Site)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    with sync_playwright() as playwright:
        executable = shutil.which("google-chrome") or shutil.which("chromium")
        browser = playwright.chromium.launch(headless=True,
                                             executable_path=executable)
        for name, width, height, scale in (
            ("screenshot.png", 1440, 960, 1),
            ("mobile.png", 390, 844, 2),
        ):
            page = browser.new_page(viewport={"width": width, "height": height},
                                    device_scale_factor=scale,
                                    reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(url, wait_until="networkidle")
            page.locator("#engine").get_by_text("Renderer unavailable").count() == 0
            assert page.locator("h1").count() == 1
            assert page.locator("#dropzone").is_visible()
            assert not errors, errors
            page.screenshot(path=str(DOCS / name), full_page=True, animations="disabled")
            page.close()
        browser.close()
    server.shutdown()
    print("Saved genuine UI screenshots: docs/screenshot.png and docs/mobile.png")


if __name__ == "__main__":
    main()
