"""A project's document store on the fleet web machine: filled live from the hosts, read by the library."""

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from fleet.composition import open_workspace
from fleet.remote import fleetd
from fleet.transport import Host
from fleet.web.job_store import ProjectDocuments
from fleet.web.server import FleetState, follow_host, make_handler

real_fetch_raw = FleetState.fetch_raw


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A worker host on this machine with its own fleet home, reached through the branch's fleetd."""
    home = tmp_path / "worker"
    monkeypatch.setenv("FLEET_FLEETD_PATH", fleetd.__file__)
    monkeypatch.setenv("FLEET_REMOTE_HOME", str(home))
    monkeypatch.setattr(FleetState, "fetch_raw", real_fetch_raw)
    return home


@pytest.fixture
def project_id() -> str:
    def create(registry):
        project = registry.create("Restoke")
        registry.link(project.id, "worker", "restoke")
        return project.id
    return open_workspace().edit_registry(create)


def worker_fleetd(home: Path, *arguments: str) -> dict:
    result = subprocess.run([sys.executable, fleetd.__file__, *arguments], env={**os.environ, "FLEET_HOME": str(home)},
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@contextmanager
def deck(state: FleetState) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def get(url: str, path: str, **query: str) -> tuple[int, dict]:
    try:
        with urlopen(f"{url}{path}?{urlencode(query)}", timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def eventually(check, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return check()
        except (AssertionError, KeyError, StopIteration):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def stored_job(url: str, project_id: str) -> dict:
    project = next(project for project in get(url, "/api/library")[1]["projects"] if project["id"] == project_id)
    [job] = project["jobs"]
    return job


def test_documents_are_copied_live_and_outlive_the_job_and_the_host(
        tmp_path: Path, worker: Path, project_id: str) -> None:
    work = tmp_path / "work"
    work.mkdir()
    steps = tmp_path / "steps.json"
    steps.write_text(json.dumps(["Review the suppliers route and write findings to the outbox"]))
    job_id = worker_fleetd(worker, "create", "--project", "restoke", "--description", "Review suppliers",
                           "--agent", "claude", "--cwd", str(work), "--steps-file", str(steps), "--hold")["id"]
    state = FleetState([Host("worker", None)])
    threading.Thread(target=follow_host, args=(state, state.hosts[0]), daemon=True).start()
    with deck(state) as url:
        # the brief is stored from the moment the job is listed
        job = eventually(lambda: stored_job(url, project_id))
        assert (job["description"], job["host"], job["availability"]) == ("Review suppliers", "worker", "on host")
        key = job["key"]

        def reads(document_id: str, text: str) -> None:
            status, body = get(url, "/api/library/job", project=project_id, job=key, id=document_id)
            assert status == 200 and text in body["markdown"], (body, stored_job(url, project_id))
        eventually(lambda: reads("brief-0", "suppliers route"))

        # an outbox document is copied as it appears, and again as it changes
        findings = worker / "jobs" / job_id / "outbox" / "findings.md"
        findings.write_text("# Findings\n\nFirst pass.")
        eventually(lambda: reads("outbox-findings.md", "First pass."))
        findings.write_text("# Findings\n\nSecond pass, with the tally.")
        eventually(lambda: reads("outbox-findings.md", "Second pass"))

        # a working note the agent wrote outside its cwd, e.g. into the main checkout, is copied too
        probe = tmp_path / "restoke" / "webapp" / "ralph" / "v2-review" / "probe.md"
        probe.parent.mkdir(parents=True)
        probe.write_text("# Probe\n\nThe V2 route renders.")
        record = json.loads((worker / "jobs" / job_id / "job.json").read_text())
        record["written_documents"] = [{"path": str(probe), "step": 0}]
        (worker / "jobs" / job_id / "job.json").write_text(json.dumps(record))
        eventually(lambda: reads("file-0", "V2 route renders"))

        # fleet rm deletes the worker's job directory; the store keeps the copy
        worker_fleetd(worker, "rm", job_id)

        def gone() -> None:
            assert stored_job(url, project_id)["availability"] == "gone from host"
        eventually(gone)
        reads("outbox-findings.md", "Second pass")

    # a fleet web started while the host is unreachable still lists and reads the store
    offline = FleetState([Host("worker", "nobody@unreachable.invalid")])
    with deck(offline) as url:
        job = stored_job(url, project_id)
        assert job["availability"] == "host offline"
        assert {document["id"] for document in job["documents"]} == {"brief-0", "outbox-findings.md", "file-0"}
        status, body = get(url, "/api/library/job", project=project_id, job=key, id="outbox-findings.md")
        assert status == 200 and "Second pass" in body["markdown"]


def test_reading_stays_inside_the_projects_store(tmp_path: Path) -> None:
    store = ProjectDocuments(tmp_path / "projects")
    job = {"id": "../../escape", "project": "restoke", "description": "d", "status": "done", "created_at": 1,
           "steps": [], "documents": [{"id": "outbox-../../../x.md", "kind": "outbox", "name": "x.md",
                                       "step": None, "path": "/p", "size": 3, "mtime": 1}]}
    [document] = store.observe("p-1", "../host", job)
    store.keep("p-1", "../host", job["id"], document, "# x")
    [directory] = (tmp_path / "projects" / "p-1" / "jobs").iterdir()
    assert directory.name == "host-.._.._escape"
    assert [path.name for path in directory.iterdir() if path.suffix == ".md"] == ["outbox-.._.._.._x.md"]
    [stored] = store.jobs("p-1")
    assert store.read("p-1", stored["key"], "outbox-../../../x.md")["markdown"] == "# x"

    secret = tmp_path / "secret.md"
    secret.write_text("private")
    assert store.read("p-1", "../../..", "x") is None
    working = tmp_path / "projects" / "p-1" / "working"
    (working / "prds").mkdir(parents=True)
    (working / "prds" / "v2.md").write_text("# V2 PRD")
    (working / "leak.md").symlink_to(secret)
    (working / "linked").symlink_to(tmp_path)
    assert [document["id"] for document in store.working("p-1")] == ["prds/v2.md"]
    assert store.read_working("p-1", "prds/v2.md")["markdown"] == "# V2 PRD"
    for refused in ("leak.md", "linked/secret.md", "../../secret.md", "/etc/passwd.md", "prds/../../../secret.md"):
        assert store.read_working("p-1", refused) is None

    # a stored file swapped for a symlink out of the store is refused too
    stored_file = directory / "outbox-.._.._.._x.md"
    stored_file.unlink()
    stored_file.symlink_to(secret)
    assert store.read("p-1", stored["key"], "outbox-../../../x.md") is None


def test_an_older_hosts_local_notes_are_never_stored(tmp_path: Path) -> None:
    store = ProjectDocuments(tmp_path / "projects")
    listed = [{"id": "file-0", "kind": "file", "name": "CLAUDE.local.md", "step": 0, "path": "/w/CLAUDE.local.md",
               "size": 5, "mtime": 1},
              {"id": "outbox-report.md", "kind": "outbox", "name": "report.md", "step": None, "path": "/j/outbox/report.md",
               "size": 8, "mtime": 1}]
    job = {"id": "job1", "project": "p", "description": "d", "status": "done", "created_at": 1, "steps": [],
           "documents": listed}
    assert [document["id"] for document in store.observe("p-1", "host", job)] == ["outbox-report.md"]
    [stored] = store.jobs("p-1")
    assert [document["id"] for document in stored["documents"]] == ["outbox-report.md"]
