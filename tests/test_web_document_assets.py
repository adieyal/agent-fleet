"""Images a document links to are served only from the document's own roots, and only as images."""

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from fleet.remote import fleetd
from fleet.transport import FleetError, Host
from fleet.web.documents import ASSET_READ_LIMIT, fetch_asset, render_markdown
from fleet.web.library import ProjectLibrary
from fleet.web.server import make_handler

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>alert(1)</script></svg>'


class JobState:
    def host_names(self) -> list[str]:
        return ["host"]

    def read_asset(self, host_name: str, job_id: str, document_id: str, asset_path: str) -> tuple[str, bytes]:
        return fetch_asset(Host(host_name, None), job_id, document_id, asset_path)


@contextmanager
def serving(state: object, library: ProjectLibrary | None = None) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, library))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def image_folder(directory: Path, outside: Path) -> None:
    """Beside the document: an SVG, a text file, an oversized PNG, a link out of the roots and a hidden folder."""
    (directory / "img").mkdir(parents=True)
    (directory / "img" / "diagram.svg").write_bytes(SVG)
    (directory / "notes.txt").write_text("not an image")
    (directory / "big.png").write_bytes(b"\x89PNG" + b"\0" * ASSET_READ_LIMIT)
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"\x89PNG secret")
    (directory / "link.png").symlink_to(outside / "secret.png")


@pytest.fixture
def job_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A deck whose job assets come from a real `fleetd read-asset` over a job with an outbox report."""
    home = tmp_path / "fleet"
    outbox = home / "jobs" / "job1" / "outbox"
    image_folder(outbox, tmp_path / "outside")
    (outbox / "report.md").write_text("# Report\n\n![diagram](img/diagram.svg)\n")
    job = {"id": "job1", "project": "project", "agent": "codex", "description": "work", "steps": []}
    (outbox.parent / "job.json").write_text(json.dumps(job))

    def fleetd_call(_host: Host, arguments: list[str], **_kwargs: object) -> dict:
        result = subprocess.run([sys.executable, fleetd.__file__, *arguments],
                                env={**os.environ, "FLEET_HOME": str(home)}, capture_output=True, text=True, check=False)
        payload = json.loads(result.stdout)
        if "error" in payload:
            raise FleetError(payload["error"])
        return payload

    monkeypatch.setattr("fleet.web.documents.transport.call", fleetd_call)
    with serving(JobState()) as url:
        yield url + "/api/doc/asset?" + urlencode({"host": "host", "job": "job1", "id": "outbox-report.md"})


@pytest.fixture
def library_server(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "project"
    image_folder(root / "docs", tmp_path / "outside")
    (root / "docs" / "guide.md").write_text("# Guide\n\n![diagram](img/diagram.svg)\n")
    (root / ".private").mkdir()
    (root / ".private" / "hidden.png").write_bytes(b"\x89PNG hidden")
    with serving(JobState(), ProjectLibrary({"project": str(root)})) as url:
        yield url + "/api/library/asset?" + urlencode({"project": "project", "id": "docs/guide.md"})


def get(url: str, path: str) -> tuple[int, dict[str, str], bytes]:
    try:
        with urlopen(f"{url}&{urlencode({'path': path})}", timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers), error.read()


@pytest.mark.parametrize("server", ["job_server", "library_server"])
def test_an_svg_beside_the_document_is_served_inert(server: str, request: pytest.FixtureRequest) -> None:
    status, headers, body = get(request.getfixturevalue(server), "img/diagram.svg")
    assert status == 200
    assert body == SVG
    assert headers["Content-Type"] == "image/svg+xml"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in headers["Content-Security-Policy"] and "default-src 'none'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("server,path,status", [
    ("job_server", "../../../../outside/secret.png", 403),
    ("library_server", "../../outside/secret.png", 403),
    ("library_server", "../.private/hidden.png", 403),
    *[(server, path, status) for server in ("job_server", "library_server") for path, status in [
        ("link.png", 403), ("/etc/hostname", 403), ("notes.txt", 415), ("big.png", 413), ("img/missing.png", 404)]],
])
def test_images_outside_the_roots_other_types_and_oversized_files_are_refused(
        server: str, path: str, status: int, request: pytest.FixtureRequest) -> None:
    code, headers, body = get(request.getfixturevalue(server), path)
    assert code == status
    assert headers["Content-Type"] == "application/json"
    assert b"\x89PNG" not in body


def test_rendering_escapes_html_in_prose_code_and_diagrams() -> None:
    html = render_markdown("<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n"
                           "```mermaid\ngraph TD; A[<img src=x onerror=alert(1)>]\n```\n\n"
                           "```python\nprint('<b>')\n```\n")["html"]
    assert "<script>" not in html and "<img src=x" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert '<code class="language-mermaid">' in html and '<code class="language-python">' in html
