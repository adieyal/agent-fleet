"""Serves the deck dashboard and pushes live fleet state to it over server-sent events.

Each host has one long-lived `fleetd stream` over ssh; job changes arrive as they
happen and are fanned out to every connected browser.
"""
from __future__ import annotations

import json
from copy import deepcopy
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

from fleet import building, projects, transport
from fleet.composition import open_attention, open_execution, open_library, open_store
from fleet.infrastructure.sqlite import Store
from fleet.modules.attention import InputObservation, ItemResolved
from fleet.building import DEFAULT_CAPACITY, NoVacancy
from fleet.workspace import FOCUSES, AlreadyShuttered, NotShuttered, WorkspaceStore
from fleet.projects import Registry
from fleet.transport import FleetError, Host
from fleet.web.documents import DocumentAccessDenied, fetch_document
from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.library import ProjectLibrary
from fleet.web.live import AlreadyHoused, LiveWorkspace
from fleet.web.ingester import observe_runs

WEB_ROOT = Path(__file__).parent.resolve()
INDEX_PATH = WEB_ROOT / "index.html"
APP_DIRECTORIES = ("css", "js")  # the deck's own code, read at startup together with the page
STATIC_PREFIXES = ("/vendor/", "/assets/")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
                ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".json": "application/json",
                ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
ATTENTION_ACTIONS = ("acknowledge", "snooze", "reopen")
FLOOR_CHANGES = ("/api/move-in", "/api/link", "/api/merge", "/api/shutter", "/api/restore")
EVENTS_PER_JOB = "15"
STREAM_SILENCE_LIMIT = 20  # seconds without a heartbeat before the stream is considered dead
RECONNECT_DELAY = 3
SSE_PING_INTERVAL = 10
SSE_COALESCE = 0.1  # batch bursts of changes into one push


class FleetState(LiveWorkspace):
    """Live jobs and interactive sessions per host plus a version counter that browsers wait on.

    Hosts running a fleetd older than session support never send session lines, so
    their `sessions` list simply stays empty.

    Each job and session keeps its host-local `project` label and gains `project_id`,
    resolved from the registry's explicit (host, label) links; unlinked labels get
    null. The registry is re-read for every document so CLI edits show without a
    restart. If a re-read fails, the last good registry is used and `projects_error`
    says why.

    Each also gains `focus`, from its project when linked and its label otherwise; the
    document carries the stored choices under `focus` and stored attention items
    under `attention`. The deck
    writes only the user's choices: focus, and acknowledging or snoozing an item.
    """

    def __init__(self, hosts: list[Host], project_labels: dict[str, str] | None = None,
                 load_registry: Callable[[], Registry] | None = None,
                 workspace: WorkspaceStore | None = None,
                 load_capacity: Callable[[], int] | None = None,
                 pipelines: dict[str, dict[str, str]] | None = None,
                 store: Store | None = None) -> None:
        self.hosts = hosts
        self.project_labels = project_labels or {}
        self.load_registry = load_registry or Registry
        self.registry = self.load_registry()
        self.load_capacity = load_capacity or (lambda: DEFAULT_CAPACITY)
        self.capacity = self.load_capacity()
        self.workspace = workspace or WorkspaceStore(None)
        self.store = store if store is not None else open_store()
        self.attention = open_attention(self.store, workspace_path=self.workspace.path)
        self.execution = open_execution(self.store)
        self.run_library = open_library(self.store)
        self.woken_until = 0.0
        self.changed = threading.Condition()
        self.version = 0
        self.history_cursor = self.store.latest_sequence()
        self.by_host: dict[str, dict[str, Any]] = {
            host.name: {"name": host.name, "ok": False, "error": "connecting…", "jobs": {}, "sessions": {}}
            for host in hosts}
        self.pipeline_config = pipelines or {}
        self.pipeline_runs = {}
        self.pipeline_seq = 0

    def follow_history(self, stop: threading.Event) -> None:
        while not stop.is_set():
            changes = self.store.history_after(self.history_cursor)
            if changes:
                self.history_cursor = int(changes[-1]["sequence"])
                self.bump()
            stop.wait(0.25)

    def update(self, host_name: str, mutate: Any, *, owners: set[str] | None = None,
               ingest: bool = True, heartbeat: bool = False) -> None:
        with self.changed:
            previous = deepcopy(self.by_host[host_name])
            sequence = self.store.latest_sequence()
            mutate(self.by_host[host_name])
            self.by_host[host_name] = deepcopy(self.by_host[host_name])
            reconciled = False
            if ingest:
                host = self.by_host[host_name]
                observe_runs(self.execution, self.run_library, host)
                reconciled = self.attention.observe({**host,
                    "jobs": [self.registry.resolve(host_name, job) for job in host["jobs"].values()],
                    "sessions": [self.registry.resolve(host_name, session) for session in host["sessions"].values()]},
                    owners=owners, raise_items=not heartbeat)
            if previous == self.by_host[host_name] and self.store.latest_sequence() == sequence and not reconciled:
                return
            self.version += 1
            self.changed.notify_all()

    def refresh_registry(self) -> str | None:
        try:
            self.registry = self.load_registry()
            return None
        except (FleetError, ValueError, KeyError, TypeError) as error:
            return f"project registry not reloaded: {error}"

    def refresh_capacity(self) -> str | None:
        try:
            self.capacity = self.load_capacity()
            return None
        except (FleetError, ValueError) as error:
            return f"capacity not reloaded: {error}"

    def known_projects(self) -> dict[str, Any]:
        self.refresh_registry()   # a project registered a moment ago can be focused
        return self.registry.projects

    def edit_registry(self, change: Callable[[Registry], Any]) -> Any:
        registry = self.load_registry()
        result = change(registry)
        projects.save_registry(registry)
        self.registry = registry
        return result

    def repository_remotes(self, host: str, directories: list[str]) -> dict[str, list[str]]:
        return transport.repository_remotes(next(known for known in self.hosts if known.name == host), directories)

    def document(self) -> dict[str, Any]:
        projects_error = self.refresh_registry()
        capacity_error = self.refresh_capacity()
        registry = self.registry
        with self.changed:
            document = self.with_attention({"time": time.time(), "project_labels": self.project_labels,
                    "projects": [{"id": project_id, **entry} for project_id, entry in registry.to_config().items()],
                    "projects_error": projects_error, "hosts": [
                {**{key: value for key, value in self.by_host[host.name].items() if key not in ("jobs", "sessions")},
                 "jobs": [self.workspace.annotate(registry.resolve(host.name, job)) for job in
                          sorted(self.by_host[host.name]["jobs"].values(), key=lambda job: job["created_at"])],
                 "sessions": [self.workspace.annotate(registry.resolve(host.name, session)) for session in
                              sorted(self.by_host[host.name]["sessions"].values(),
                                     key=lambda session: session.get("started_at") or 0)]}
                for host in self.hosts]})
        document = self.with_building(document, registry)
        document["building"]["capacity_error"] = capacity_error
        document["pipelines"] = self.pipelines(registry, self.by_host)
        return document

    def pipeline_updates(self, after: int) -> list[dict[str, Any]]:
        return self.pipelines(self.registry, self.by_host, after)

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
    if kind == "input_observation":
        observation = InputObservation(**{key: message[key] for key in InputObservation.__dataclass_fields__})
        project = state.registry.resolve(host.name, {"project": observation.project})
        state.attention.observe_input(host.name, observation, project_id=project.get("project_id"))
        state.bump()
        return
    if kind == "hello":
        state.update(host.name, lambda entry: entry.update(ok=True, error=None, jobs={}, sessions={}), ingest=False)
    elif kind == "job":
        job = message["job"]
        state.update(host.name, lambda entry: entry["jobs"].__setitem__(job["id"], job),
                     owners={f"job:{host.name}:{job['id']}"})
    elif kind == "removed":
        state.update(host.name, lambda entry: entry["jobs"].pop(message["id"], None),
                     owners={f"job:{host.name}:{message['id']}"})
    elif kind == "session":
        session = message["session"]
        state.update(host.name, lambda entry: entry["sessions"].__setitem__(session["id"], session),
                     owners={f"session:{host.name}:{session['id']}"})
    elif kind == "session_removed":
        state.update(host.name, lambda entry: entry["sessions"].pop(message["id"], None),
                     owners={f"session:{host.name}:{message['id']}"})
    elif kind == "heartbeat":
        state.update(host.name, lambda entry: None, heartbeat=True)
    elif kind == "pipeline" and isinstance(message.get("pipeline"), str):
        state.report_pipeline(host.name, message["pipeline"], message.get("run"), message.get("baseline"))
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
            elif path == "/api/move-in":
                self.move_in_options()
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

        def do_POST(self) -> None:  # noqa: N802 — http.server naming
            path = self.path.split("?", 1)[0]
            action = path.removeprefix("/api/attention/") if path.startswith("/api/attention/") else None
            if path not in FLOOR_CHANGES + ("/api/focus",) and action not in ATTENTION_ACTIONS:
                self.respond(404, "text/plain", b"not found")
            elif not self.same_origin():
                self.respond(403, "application/json", b'{"error": "cross-origin writes are refused"}')
            elif self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.respond(415, "application/json", b'{"error": "send JSON"}')
            else:
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                except ValueError:
                    body = None
                body = body if isinstance(body, dict) else {}
                if action:
                    self.attention(action, body)
                elif path in ("/api/move-in", "/api/link"):
                    self.move_in(path.removeprefix("/api/"), body)
                elif path == "/api/merge":
                    self.merge(body)
                elif path in ("/api/shutter", "/api/restore"):
                    self.storehouse(path.removeprefix("/api/"), body)
                else:
                    self.focus(body)

        def same_origin(self) -> bool:
            """Browsers send Origin on every POST; a page from another site must not change anything."""
            origin = self.headers.get("Origin")
            return origin is None or urlsplit(origin).netloc == self.headers.get("Host")

        def error(self, status: int, message: str) -> None:
            self.respond(status, "application/json", json.dumps({"error": message}).encode())

        def focus(self, body: dict[str, Any]) -> None:
            """POST /api/focus {"focus": "priority" | "background", "projects": [id, …], "labels": [label, …]}

            Projects are focused by ID; labels are for jobs and sessions with no linked project.
            """
            project_ids, labels = body.get("projects", []), body.get("labels", [])
            if (body.get("focus") not in FOCUSES or not isinstance(project_ids, list) or not isinstance(labels, list)
                    or not all(isinstance(name, str) for name in project_ids + labels) or not project_ids + labels):
                self.error(400, "focus (priority or background) and some projects or labels are required")
                return
            try:
                state.set_focus(body["focus"], project_ids, labels)
            except FleetError as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(state.workspace.focus_snapshot()).encode())

        def move_in_options(self) -> None:
            """GET /api/move-in?label=&host=&host=… — projects the label on those hosts may belong to, to offer
            linking before a new project."""
            query = parse_qs(urlsplit(self.path).query)
            try:
                options = state.move_in_options((query.get("label") or [""])[0], query.get("host", []))
            except FleetError as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(options).encode())

        def move_in(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/move-in {"hosts": [host, …], "label": label, "shutter": project ID to make room, if full}
            — register the label on those hosts as one project on the lowest free floor.
            POST /api/link {"project": ID, "hosts": [host, …], "label": label} — link it to that project instead.
            A single "host" may stand for "hosts"."""
            hosts = body.get("hosts", [body["host"]] if "host" in body else [])
            if (not isinstance(hosts, list) or not all(isinstance(host, str) for host in hosts)
                    or not isinstance(body.get("label"), str) or not isinstance(body.get("shutter", ""), str)
                    or (action == "link" and not isinstance(body.get("project"), str))):
                self.error(400, "hosts and a label are required" + (", and a project" if action == "link" else ""))
                return
            if action == "link":
                self.change_floors(lambda: state.link_in(body["project"], hosts, body["label"]))
            else:
                self.change_floors(lambda: state.move_in(hosts, body["label"], body.get("shutter")))

        def merge(self, body: dict[str, Any]) -> None:
            """POST /api/merge {"keep": ID, "other": ID} — fold a project registered by mistake into the older one."""
            if not isinstance(body.get("keep"), str) or not isinstance(body.get("other"), str):
                self.error(400, "keep and other are required")
                return
            self.change_floors(lambda: state.merge(body["keep"], body["other"]))

        def storehouse(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/shutter {"project": ID} — pack it away and free its floor.
            POST /api/restore {"project": ID, "shutter": project ID to make room, if full} — move its crate back in."""
            if not isinstance(body.get("project"), str) or not isinstance(body.get("shutter", ""), str):
                self.error(400, "project is required")
                return
            if action == "shutter":
                self.change_floors(lambda: state.shutter(body["project"]))
            else:
                self.change_floors(lambda: state.restore(body["project"], body.get("shutter")))

        def change_floors(self, change: Callable[[], dict[str, Any]]) -> None:
            try:
                changed = change()
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except (NoVacancy, AlreadyHoused, AlreadyShuttered, NotShuttered) as error:
                self.error(409, str(error))
                return
            except FleetError as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(changed).encode())

        def attention(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/attention/acknowledge|snooze|reopen {"id": item id, "seconds": snooze length}"""
            if not isinstance(body.get("id"), str):
                self.error(400, "the item's id is required")
                return
            try:
                state.act_on_attention(action, body["id"], body.get("seconds"))
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except ItemResolved as error:
                self.error(409, str(error))
                return
            except (FleetError, ValueError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps({"id": body["id"], "action": action}).encode())

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
            except DocumentAccessDenied as error:
                self.respond(403, "application/json", json.dumps({"error": str(error)}).encode())
                return
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
            version, pipeline_seq = -1, 0
            try:
                while True:
                    new_version = state.wait_for_change(version, timeout=SSE_PING_INTERVAL, seen_pipelines=pipeline_seq)
                    if new_version != version:
                        time.sleep(SSE_COALESCE)
                        version, pipeline_seq = state.version, state.pipeline_seq
                        payload = json.dumps(state.document())   # carries every pipeline as it is now
                        self.wfile.write(f"event: state\ndata: {payload}\n\n".encode())
                    elif state.pipeline_seq != pipeline_seq:
                        updates = state.pipeline_updates(pipeline_seq)
                        pipeline_seq = max([pipeline_seq] + [update["seq"] for update in updates])
                        for update in updates:
                            self.wfile.write(f"event: pipeline\ndata: {json.dumps(update)}\n\n".encode())
                    else:
                        self.wfile.write(b"event: ping\ndata: {}\n\n")
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


def workspace_path() -> Path:
    """Live workspace state sits beside the Fleet config, outside Git."""
    return transport.config_path().parent / "workspace.json"


def serve(hosts: list[Host], *, port: int, bind: str, open_browser: bool = False,
          libraries: dict[str, str] | None = None, project_labels: dict[str, str] | None = None,
          pipelines: dict[str, dict[str, str]] | None = None) -> None:
    state = FleetState(hosts, project_labels, projects.load_registry, WorkspaceStore(workspace_path()),
                       building.load_capacity, pipelines, open_store())
    threading.Thread(target=state.follow_history, args=(threading.Event(),), daemon=True).start()
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
