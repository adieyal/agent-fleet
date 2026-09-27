"""Smoke checks for the dashboard's HTTP boundary."""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from fleet.composition import open_store
from fleet.web.documents import renderer
from fleet.web.library import ProjectLibrary
from fleet.web.server import FleetState, make_handler
from fleet.workspace import WorkspaceStore


class DashboardHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        directory = TemporaryDirectory(prefix="fleet-http-test-")
        cls.addClassCleanup(directory.cleanup)
        root = Path(directory.name)
        state = FleetState([], store=open_store(root / "fleet.db"), workspace=WorkspaceStore(root / "workspace.json"))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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

    def test_project_room_label_is_separate_from_project_identity(self) -> None:
        state = FleetState([], {"restoke-analytics": "Bang bang!"})
        self.assertEqual(state.document()["project_labels"], {"restoke-analytics": "Bang bang!"})

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

    def test_library_lists_and_reads_only_configured_markdown(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "project"
            (root / "docs").mkdir(parents=True)
            (root / "README.md").write_text("# Project overview\n")
            (root / "docs" / "plan.md").write_text("# Plan\n<script>bad()</script>\n")
            (root / "private.md").symlink_to(base / "private.md")
            (base / "private.md").write_text("# Secret\n")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FleetState([]), ProjectLibrary({"example": str(root)})))
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base_url + "/api/library", timeout=5) as response:
                    documents = json.load(response)["documents"]
                self.assertEqual({doc["id"] for doc in documents}, {"README.md", "docs/plan.md"})
                self.assertIn("Plan", {doc["title"] for doc in documents})
                (root / ".fleet").mkdir()
                manifest = root / ".fleet" / "library.json"
                manifest.write_text(json.dumps({"order": ["docs/plan.md", "README.md"]}))
                with urlopen(base_url + "/api/library", timeout=5) as response:
                    ordered = json.load(response)["documents"]
                self.assertEqual([doc["id"] for doc in ordered], ["docs/plan.md", "README.md"])
                manifest.write_text(json.dumps({"order": ["docs/plan.md"], "hide": ["README.md"]}))
                with urlopen(base_url + "/api/library", timeout=5) as response:
                    curated = json.load(response)["documents"]
                self.assertEqual([doc["id"] for doc in curated], ["docs/plan.md"])
                manifest.write_text(json.dumps({"order": ["README.md"], "show_unlisted": False}))
                with urlopen(base_url + "/api/library", timeout=5) as response:
                    selected = json.load(response)["documents"]
                self.assertEqual([doc["id"] for doc in selected], ["README.md"])
                query = urlencode({"project": "example", "id": "docs/plan.md"})
                with urlopen(base_url + "/api/library/doc?" + query, timeout=5) as response:
                    document = json.load(response)
                self.assertIn("&lt;script&gt;", document["html"])
                self.assertNotIn("<script>", document["html"])
                for document_id in ("private.md", "../private.md", "docs/../private.md"):
                    query = urlencode({"project": "example", "id": document_id})
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(base_url + "/api/library/doc?" + query, timeout=5)
                    self.assertEqual(raised.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
