"""Serves the deck dashboard and pushes live fleet state to it over server-sent events.

Each host has one long-lived `fleetd stream` over ssh; job changes arrive as they
happen and are fanned out to every connected browser.
"""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from fleet import projects, transport
from fleet.projects import Registry
from fleet.transport import FleetError, Host
from fleet.web.documents import fetch_document
from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.library import ProjectLibrary

WEB_ROOT = Path(__file__).parent.resolve()
INDEX_PATH = WEB_ROOT / "index.html"
APP_DIRECTORIES = ("css", "js")  # the deck's own code, read at startup together with the page
STATIC_PREFIXES = ("/vendor/", "/assets/")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
                ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".json": "application/json",
                ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
EVENTS_PER_JOB = "15"
STREAM_SILENCE_LIMIT = 20  # seconds without a heartbeat before the stream is considered dead
RECONNECT_DELAY = 3
SSE_PING_INTERVAL = 10
SSE_COALESCE = 0.1  # batch bursts of changes into one push


class FleetState:
    """Live jobs and interactive sessions per host plus a version counter that browsers wait on.

    Hosts running a fleetd older than session support never send session lines, so
    their `sessions` list simply stays empty.

    Each job and session keeps its host-local `project` label and gains `project_id`,
    resolved from the registry's explicit (host, label) links; unlinked labels get
    null. The registry is re-read for every document so CLI edits show without a
    restart. If a re-read fails, the last good registry is used and `projects_error`
    says why.
    """

    def __init__(self, hosts: list[Host], project_labels: dict[str, str] | None = None,
                 load_registry: Callable[[], Registry] | None = None) -> None:
        self.hosts = hosts
        self.project_labels = project_labels or {}
        self.load_registry = load_registry or Registry
        self.registry = self.load_registry()
        self.changed = threading.Condition()
        self.version = 0
        self.by_host: dict[str, dict[str, Any]] = {
            host.name: {"name": host.name, "ok": False, "error": "connecting…", "jobs": {}, "sessions": {}}
            for host in hosts}

    def update(self, host_name: str, mutate: Any) -> None:
        with self.changed:
            mutate(self.by_host[host_name])
            self.version += 1
            self.changed.notify_all()

    def refresh_registry(self) -> str | None:
        try:
            self.registry = self.load_registry()
            return None
        except (FleetError, ValueError, KeyError, TypeError) as error:
            return f"project registry not reloaded: {error}"

    def document(self) -> dict[str, Any]:
        projects_error = self.refresh_registry()
        registry = self.registry

        def resolved(host_name: str, item: dict[str, Any]) -> dict[str, Any]:
            project = registry.project_for(host_name, item["project"]) if item.get("project") else None
            return {**item, "project_id": project.id if project else None}

        with self.changed:
            return {"time": time.time(), "project_labels": self.project_labels,
                    "projects": [{"id": project_id, **entry} for project_id, entry in registry.to_config().items()],
                    "projects_error": projects_error, "hosts": [
                {**{key: value for key, value in self.by_host[host.name].items() if key not in ("jobs", "sessions")},
                 "jobs": [resolved(host.name, job) for job in
                          sorted(self.by_host[host.name]["jobs"].values(), key=lambda job: job["created_at"])],
                 "sessions": [resolved(host.name, session) for session in
                              sorted(self.by_host[host.name]["sessions"].values(),
                                     key=lambda session: session.get("started_at") or 0)]}
                for host in self.hosts]}

    def wait_for_change(self, seen_version: int, timeout: float) -> int:
        with self.changed:
            self.changed.wait_for(lambda: self.version != seen_version, timeout=timeout)
            return self.version

    def host_names(self) -> list[str]:
        return [host.name for host in self.hosts]

    def read_document(self, host_name: str, job_id: str, document_id: str) -> dict[str, Any]:
        host = next(host for host in self.hosts if host.name == host_name)
        return fetch_document(host, job_id, document_id)


def follow_host(state: FleetState, host: Host) -> None:
    """Keep one `fleetd stream` running for the host, reconnecting when it dies or goes quiet."""
    while True:
        error = run_stream(state, host)

        def mark_down(entry: dict[str, Any]) -> None:
            entry.update(ok=False, error=error, jobs={}, sessions={})

        state.update(host.name, mark_down)
        time.sleep(RECONNECT_DELAY)


def run_stream(state: FleetState, host: Host) -> str:
    """Apply stream messages until the stream ends; return why it ended."""
    try:
        transport.ensure_master(host)
    except subprocess.TimeoutExpired:
        return "ssh connect timed out"
    process = subprocess.Popen(host.fleetd_command(["stream", "--events", EVENTS_PER_JOB]),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""
    try:
        while True:
            if not selector.select(timeout=STREAM_SILENCE_LIMIT):
                return f"no heartbeat for {STREAM_SILENCE_LIMIT}s"
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                process.wait(timeout=5)
                stderr_lines = process.stderr.read().decode(errors="replace").strip().splitlines()
                return stderr_lines[-1] if stderr_lines else f"stream ended (exit {process.returncode})"
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                if line.strip():
                    apply_message(state, host, json.loads(line))
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()


def apply_message(state: FleetState, host: Host, message: dict[str, Any]) -> None:
    kind = message.get("type")
    if kind == "hello":
        state.update(host.name, lambda entry: entry.update(ok=True, error=None, jobs={}, sessions={}))
    elif kind == "job":
        job = message["job"]
        state.update(host.name, lambda entry: entry["jobs"].__setitem__(job["id"], job))
    elif kind == "removed":
        state.update(host.name, lambda entry: entry["jobs"].pop(message["id"], None))
    elif kind == "session":
        session = message["session"]
        state.update(host.name, lambda entry: entry["sessions"].__setitem__(session["id"], session))
    elif kind == "session_removed":
        state.update(host.name, lambda entry: entry["sessions"].pop(message["id"], None))
    elif kind == "error":
        state.update(host.name, lambda entry: entry.update(ok=False, error=message.get("error")))


def make_handler(state: FleetState | FixtureState,
                 library: ProjectLibrary | FixtureLibrary | None = None) -> type[BaseHTTPRequestHandler]:
    # Read once so a running server keeps serving the page and code that match its API.
    index_page = INDEX_PATH.read_bytes()
    app_files = {"/" + path.relative_to(WEB_ROOT).as_posix(): path.read_bytes()
                 for directory in APP_DIRECTORIES for path in sorted((WEB_ROOT / directory).rglob("*"))
                 if path.is_file()}
    library = library or ProjectLibrary({})

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server naming
            path = self.path.split("?", 1)[0]
            if path == "/api/stream":
                self.stream()
            elif path == "/api/doc":
                self.document()
            elif path == "/api/library":
                try:
                    documents = library.list()
                except ValueError as error:
                    self.respond(400, "application/json", json.dumps({"error": str(error)}).encode())
                    return
                self.respond(200, "application/json", json.dumps({"documents": documents}).encode())
            elif path == "/api/library/doc":
                self.library_document()
            elif path == "/api/state":
                self.respond(200, "application/json", json.dumps(state.document()).encode())
            elif path in ("/", "/index.html"):
                self.respond(200, "text/html; charset=utf-8", index_page)
            elif path in app_files:
                self.respond(200, STATIC_TYPES.get(Path(path).suffix, "application/octet-stream"), app_files[path])
            elif path.startswith(STATIC_PREFIXES):
                self.static_file(path)
            else:
                self.respond(404, "text/plain", b"not found")

        def static_file(self, path: str) -> None:
            """Vendored libraries and 3D assets; anything resolving outside those folders is refused."""
            target = (WEB_ROOT / unquote(path).lstrip("/")).resolve()
            allowed = any(target.is_relative_to(WEB_ROOT / prefix.strip("/")) for prefix in STATIC_PREFIXES)
            if not allowed or not target.is_file():
                self.respond(404, "text/plain", b"not found")
                return
            content_type = STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self.respond(200, content_type, target.read_bytes(), cache_seconds=3600)

        def document(self) -> None:
            """GET /api/doc?host=&job=&id= — one rendered document; ids come from a job's `documents` list."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if query.get("host") not in state.host_names() or not query.get("job") or not query.get("id"):
                self.respond(400, "application/json", b'{"error": "host, job and id are required"}')
                return
            try:
                body = state.read_document(query["host"], query["job"], query["id"])
            except FleetError as error:
                self.respond(404, "application/json", json.dumps({"error": str(error)}).encode())
                return
            self.respond(200, "application/json", json.dumps(body).encode())

        def library_document(self) -> None:
            """GET /api/library/doc?project=&id= — Markdown under a configured local root."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("project") or not query.get("id"):
                self.respond(400, "application/json", b'{"error": "project and id are required"}')
                return
            body = library.read(query["project"], query["id"])
            if body is None:
                self.respond(404, "application/json", b'{"error": "document not found"}')
                return
            self.respond(200, "application/json", json.dumps(body).encode())

        def stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            version = -1
            try:
                while True:
                    new_version = state.wait_for_change(version, timeout=SSE_PING_INTERVAL)
                    if new_version == version:
                        self.wfile.write(b"event: ping\ndata: {}\n\n")
                    else:
                        time.sleep(SSE_COALESCE)
                        version = state.version
                        payload = json.dumps(state.document())
                        self.wfile.write(f"event: state\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def respond(self, status: int, content_type: str, body: bytes, *, cache_seconds: int = 0) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", f"max-age={cache_seconds}" if cache_seconds else "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *arguments: Any) -> None:  # noqa: A002 — silence access log
            pass

    return Handler


def serve(hosts: list[Host], *, port: int, bind: str, open_browser: bool = False,
          libraries: dict[str, str] | None = None, project_labels: dict[str, str] | None = None) -> None:
    state = FleetState(hosts, project_labels, projects.load_registry)
    for host in hosts:
        threading.Thread(target=follow_host, args=(state, host), daemon=True).start()
    run_server(make_handler(state, ProjectLibrary(libraries or {})), port=port, bind=bind, open_browser=open_browser)


def serve_fixture(path: str, *, port: int, bind: str, open_browser: bool = False) -> None:
    """Serve a recorded fleet from JSON (see fleet.web.fixture); no hosts are contacted."""
    state = FixtureState.load(path)
    run_server(make_handler(state, FixtureLibrary(state.fixture)), port=port, bind=bind, open_browser=open_browser)


def run_server(handler: type[BaseHTTPRequestHandler], *, port: int, bind: str, open_browser: bool) -> None:
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    url = f"http://{'localhost' if bind in ('127.0.0.1', '0.0.0.0') else bind}:{port}/"
    print(f"fleet deck at {url}  (demo: {url}?demo)", flush=True)
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()
