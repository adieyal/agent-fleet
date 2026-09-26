"""Smoke checks for the dashboard's HTTP boundary."""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from fleet.web.documents import renderer
from fleet.web.server import FleetState, make_handler


class DashboardHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FleetState([])))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_dashboard_and_state_are_served(self) -> None:
        with urlopen(self.base_url + "/", timeout=5) as response:
            self.assertIn(b"<html", response.read().lower())
        with urlopen(self.base_url + "/api/state", timeout=5) as response:
            self.assertEqual(json.load(response)["hosts"], [])

    def test_assets_are_served_but_paths_cannot_escape(self) -> None:
        with urlopen(self.base_url + "/assets/CREDITS.md", timeout=5) as response:
            self.assertIn(b"Third-party assets", response.read())
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.base_url + "/assets/%2e%2e/server.py", timeout=5)
        self.assertEqual(raised.exception.code, 404)

    def test_agent_markdown_cannot_add_raw_html(self) -> None:
        html = renderer.render("<script>alert(1)</script>\n")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
