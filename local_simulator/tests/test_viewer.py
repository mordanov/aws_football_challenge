import functools
import http.server
import importlib.resources
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright


class ViewerTests(unittest.TestCase):
    def test_saved_cli_artifact_renders_and_bad_version_is_visible(self):
        viewer = importlib.resources.files("football_lab").joinpath("viewer")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(viewer))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                artifact = Path(directory) / "replay.json"
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "football_lab",
                        "--seed",
                        "7",
                        "--output",
                        str(artifact),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        page = browser.new_page()
                        page.goto(f"http://127.0.0.1:{server.server_port}/")
                        page.locator("#artifact").set_input_files(str(artifact))
                        page.wait_for_function(
                            "document.querySelector('#status').textContent"
                            " === 'Wiring check complete'"
                        )
                        self.assertEqual(page.locator("#players circle").count(), 10)
                        self.assertIn("seed 7", page.locator("#metadata").inner_text())
                        self.assertIn('"team_id": 1', page.locator("#attempts").inner_text())
                        invalid = Path(directory) / "invalid.json"
                        payload = json.loads(artifact.read_text())
                        payload["schema_version"] = 2
                        invalid.write_text(json.dumps(payload))
                        page.locator("#artifact").set_input_files(str(invalid))
                        page.wait_for_function(
                            "document.querySelector('#status').textContent"
                            ".startsWith('Replay error:')"
                        )
                        self.assertEqual(page.locator("#players circle").count(), 0)
                    finally:
                        browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
