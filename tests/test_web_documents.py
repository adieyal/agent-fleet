"""The document endpoint reports rejected paths explicitly."""

import json
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from fleet.transport import FleetError, Host
from fleet.remote import fleetd
from fleet.web.documents import fetch_document
from fleet.web.server import make_handler


class DocumentState:
    def host_names(self) -> list[str]:
        return ["host"]

    def read_document(self, host_name: str, job_id: str, document_id: str) -> dict:
        return fetch_document(Host(host_name, None), job_id, document_id)


@pytest.mark.parametrize("document_id", ["../secret.md", "/etc/passwd"])
def test_api_doc_refuses_path_ids_with_an_explicit_error(
    document_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_call(*args: object, **kwargs: object) -> None:
        pytest.fail("unsafe document id reached the worker")

    monkeypatch.setattr("fleet.web.documents.transport.call", unexpected_call)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DocumentState()))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        query = urlencode({"host": "host", "job": "job1", "id": document_id})
        with pytest.raises(HTTPError) as raised:
            urlopen(f"http://127.0.0.1:{server.server_port}/api/doc?{query}", timeout=5)
        assert raised.value.code == 403
        assert "document path" in json.load(raised.value)["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("kind", ["symlink", "not markdown"])
def test_api_doc_refuses_recorded_paths_outside_roots(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "fleet"
    directory = home / "jobs" / "job1"
    directory.mkdir(parents=True)
    secret = tmp_path / "private.md"
    secret.write_text("secret content")
    if kind == "symlink":
        path = directory / "link.md"
        path.symlink_to(secret)
    else:   # a written Markdown file outside the roots is the agent's own and readable; nothing else is
        path = tmp_path / "private.txt"
        path.write_text("secret content")
    job = {"id": "job1", "project": "project", "agent": "codex", "description": "work",
           "steps": [], "written_documents": [{"path": str(path), "step": 0}]}
    (directory / "job.json").write_text(json.dumps(job))

    def refused(_host: Host, arguments: list[str], **_kwargs: object) -> dict:
        result = subprocess.run([sys.executable, fleetd.__file__, *arguments],
                                env={**os.environ, "FLEET_HOME": str(home)}, capture_output=True, text=True,
                                check=False)
        payload = json.loads(result.stdout)
        if "error" in payload:
            raise FleetError(payload["error"])
        return payload

    monkeypatch.setattr("fleet.web.documents.transport.call", refused)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DocumentState()))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        query = urlencode({"host": "host", "job": "job1", "id": "file-0"})
        with pytest.raises(HTTPError) as raised:
            urlopen(f"http://127.0.0.1:{server.server_port}/api/doc?{query}", timeout=5)
        assert raised.value.code == 403
        assert "outside approved document roots" in json.load(raised.value)["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
