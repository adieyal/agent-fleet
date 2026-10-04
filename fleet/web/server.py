"""Serves the deck dashboard and pushes live fleet state to it over server-sent events.

Each host has one long-lived `fleetd stream` over ssh; job changes arrive as they
happen and are fanned out to every connected browser.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
import pickle
import os
import queue
import subprocess
import threading
import sys
import time
from datetime import datetime, timezone, timedelta
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from fleet import transport
from fleet.composition import (Store, facades, open_attention, open_execution, open_library, open_store,
                               open_workspace, open_work, open_decisions)
from fleet.modules.records import GuidanceConflict
from fleet.orchestration import promote_decision
from fleet.triage_scheduler import TriageScheduler
from fleet.composition import deliver_triage
from fleet.modules.attention import InputObservation, ItemResolved, refusal_rules
from fleet.modules.workspace import (NoVacancy, FOCUSES, AlreadyShuttered, NotShuttered,
                                     WorkspaceFacade, Registry)
from fleet.projections.workspace import annotate, resolve, registry_config
from fleet.projections.project import project_status
from fleet.projections.run_history import history_runs, run_detail
from fleet.projections.bench import bench_rooms, bench_state
from fleet.projections.history import parse_since, subject_history
from fleet.transport import FleetError, Host
from fleet.web.documents import AssetNotImage, AssetTooLarge, DocumentAccessDenied, fetch_asset, fetch_document
from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.guidance import epic_decisions, project_decisions, guidance_view
from fleet.web.job_store import DocumentKeeper, ProjectDocuments
from fleet.web.library import ProjectLibrary
from fleet.web.live import AlreadyHoused, LiveWorkspace
from fleet.web.ingester import observe_runs, observe_sessions, record_decisions

WEB_ROOT = Path(__file__).parent.resolve()
INDEX_PATH = WEB_ROOT / "index.html"
APP_DIRECTORIES = ("css", "js")  # the deck's own code, read at startup together with the page
STATIC_PREFIXES = ("/vendor/", "/assets/", "/prototype/")
PROTOTYPES = {"/prototype/bakeoff": "/prototype/bakeoff.html",  # art prototypes; not linked from the deck
              "/prototype/bench": "/prototype/bench.html",
              "/prototype/world": "/prototype/world.html",
              "/prototype/kit": "/prototype/kit.html",
              "/prototype/floor": "/prototype/floor.html",
              "/prototype/robot": "/prototype/robot.html"}
REPO_ROOT = WEB_ROOT.parent.parent


def build_id(root: Path = WEB_ROOT) -> str:
    """A fingerprint of the files the deck serves (path, size, mtime), taken at startup. Every state document carries
    it, so a page opened before a redeploy sees it change and reloads instead of mixing old code with new assets."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        stat = path.stat()
        digest.update(f"{path.relative_to(root)}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:16]


BUILD = build_id()
# Source-checkout folders the art prototypes read; absent from an installed package, so they 404 there.
CHECKOUT_FOLDERS = {"/art/bakeoff/": REPO_ROOT / "art" / "bakeoff", "/concept/": REPO_ROOT / "docs" / "images" / "concept"}
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".html": "text/html; charset=utf-8", ".hdr": "image/vnd.radiance",
                ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
                ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".json": "application/json",
                ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
ASSET_POLICY = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
ATTENTION_ACTIONS = ("acknowledge", "snooze", "reopen", "resolve", "undo-resolve", "delegate", "take")
REFUSAL_ACTIONS = ("allow", "dismiss")   # a job step's permission refusals
JOB_ACTIONS = ("answer",)   # a blocked job step's question
GUIDANCE_CHANGES = ("/api/guidance", "/api/guidance/promote")
FLOOR_CHANGES = ("/api/move-in", "/api/link", "/api/merge", "/api/shutter", "/api/restore")
EVENTS_PER_JOB = "15"
STREAM_SILENCE_LIMIT = 20  # seconds without a heartbeat before the stream is considered dead
RECONNECT_DELAY = 3
SSE_PING_INTERVAL = 10
SSE_COALESCE = 0.1  # batch bursts of changes into one push


def snapshot(value: Any) -> Any:
    """A deep copy of host state (plain JSON-shaped data); a pickle round trip is about ten times deepcopy's speed."""
    return pickle.loads(pickle.dumps(value, pickle.HIGHEST_PROTOCOL))


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
                 workspace: WorkspaceFacade | None = None,
                 load_capacity: Callable[[], int] | None = None,
                 pipelines: dict[str, dict[str, str]] | None = None,
                 store: Store | None = None, documents: ProjectDocuments | None = None) -> None:
        self.hosts = hosts
        self.project_labels = project_labels or {}
        self.store = store if store is not None else open_store()
        self.indexed: dict = {}   # library entries as last indexed (see observe_runs)
        self.taken_decisions: set = set()   # streamed decision ids already handled (see record_decisions)
        self.workspace = workspace if workspace is not None else open_workspace(self.store, actor="web-user")
        self.load_registry = load_registry or self.workspace.registry
        self.registry = self.load_registry()
        self.load_capacity = load_capacity or self.workspace.capacity
        self.capacity = self.load_capacity()
        self.attention = open_attention(self.store)
        self.execution = open_execution(self.store)
        self.run_library = open_library(self.store)
        self.decisions = open_decisions(self.store)
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
        self.documents = documents if documents is not None else ProjectDocuments()
        self.trace_retainer = transport.keep_run_trace
        self.keeper = DocumentKeeper(self.documents, self.fetch_raw, keep_trace=self.keep_trace)
        for observed in self.execution.hosts():
            if observed["name"] in self.by_host and not observed["reachable"]:
                entry = self.by_host[observed["name"]]
                entry.update(error=observed["error"], down_since=datetime.fromisoformat(observed["since"]).timestamp())
                for run in self.execution.runs():
                    if run.host != observed["name"] or run.status not in ("running", "unknown outcome"):
                        continue
                    value = {"id": run.remote_job_id, "project": run.label, "agent": run.runtime, "cwd": run.cwd,
                             "updated_at": run.last_observed.timestamp() if run.last_observed else None,
                             "workspace": run.workspace, "workspace_reason": run.workspace_reason,
                             "stale": True, "stale_since": entry["down_since"]}
                    if run.kind == "session":
                        value.update(title=run.title, status="unknown outcome", started_at=run.start.timestamp() if run.start else None)
                        entry["sessions"][run.remote_job_id] = value
                    else:
                        value.update(description=run.title, status="unknown outcome", created_at=run.start.timestamp() if run.start else None,
                                     steps=[{**step, "started_at": datetime.fromisoformat(step["start"]).timestamp() if step["start"] else None,
                                             "finished_at": datetime.fromisoformat(step["end"]).timestamp() if step["end"] else None}
                                            for step in self.execution.steps(run.id)])
                        entry["jobs"][run.remote_job_id] = value

    def fetch_raw(self, host_name: str, job_id: str, document_id: str) -> dict[str, Any]:
        """A job document's Markdown as its host serves it, before rendering."""
        host = next(host for host in self.hosts if host.name == host_name)
        return transport.call(host, ["read", job_id, document_id], timeout=30)

    def keep_documents(self, host_name: str, job: dict[str, Any]) -> None:
        """Keep job documents in its project or under its unregistered host label."""
        project_id = resolve(self.registry, host_name, job)["project_id"]
        if job.get("documents") or job.get("trace"):
            scope = project_id if project_id is not None else self.documents.label_scope(host_name, job.get("project") or "")
            self.keeper.observe(host_name, scope, job)

    def keep_trace(self, host_name: str, job: dict) -> None:
        host = next(host for host in self.hosts if host.name == host_name)
        self.trace_retainer(self.execution, host, job)

    def job_hosts(self) -> dict[str, tuple[bool, set[str]]]:
        with self.changed:
            return {name: (bool(entry["ok"]), set(entry["jobs"])) for name, entry in self.by_host.items()}

    def schedule_triage(self) -> None:
        services = facades(self.store)
        bodies = {intent['id']: json.dumps(asdict(services.decisions.get(intent['key'])), default=str)
                  for intent in services.records.intents()
                  if intent['state'] == 'pending' and intent['path'] == f"decisions/{intent['key']}.json"}
        try:
            services.records.reconcile(bodies)
        except OSError:
            logging.getLogger(__name__).exception('Records publication remains pending')
        TriageScheduler(services, lambda run, **options: deliver_triage(services, run, **options),
                        transport.host_by_name).schedule()

    def follow_history(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.schedule_triage()
            changes = self.store.history_after(self.history_cursor)
            if changes:
                self.history_cursor = int(changes[-1]["sequence"])
                self.bump()
            stop.wait(0.25)

    def update(self, host_name: str, mutate: Any, *, subjects: set[str] | None = None,
               ingest: bool = True, heartbeat: bool = False, deleted_jobs: set[str] = frozenset()) -> None:
        with self.changed:
            previous = snapshot(self.by_host[host_name])
            sequence = self.store.latest_sequence()
            mutate(self.by_host[host_name])
            entry = self.by_host[host_name]
            if not entry["ok"]:
                entry.setdefault("down_since", self.execution.clock().timestamp())
                for kind in ("jobs", "sessions"):
                    for item in entry[kind].values():
                        item.update(stale=True, stale_since=entry["down_since"])
            if not entry.get("_syncing"):
                if entry["ok"]:
                    entry.pop("down_since", None)
                self.execution.record_host(host_name, reachable=entry["ok"], error=entry["error"])
            self.by_host[host_name] = snapshot(self.by_host[host_name])
            public = lambda value: {key: item for key, item in value.items() if not key.startswith("_")}
            retry_deliveries = self.by_host[host_name]["ok"] and public(previous) != public(self.by_host[host_name])
            reconciled = False
            if ingest:
                entry = self.by_host[host_name]
                host = {**entry, **{kind: {identity: item for identity, item in entry[kind].items()
                                          if not item.get("stale")} for kind in ("jobs", "sessions")}}
                observe_runs(self.execution, self.run_library, host, self.indexed,
                             lambda job: resolve(self.registry, host_name, job)["project_id"])
                observe_sessions(self.execution, host, lambda session: resolve(self.registry, host_name, session)["project_id"])
                record_decisions(self.decisions, self.execution, self.attention, host,
                                 lambda job: resolve(self.registry, host_name, job)["project_id"], self.taken_decisions)
                reconciled = self.attention.observe({**host,
                    "jobs": [resolve(self.registry, host_name, job) for job in host["jobs"].values()],
                    "sessions": [resolve(self.registry, host_name, session) for session in host["sessions"].values()]},
                    subjects=subjects, raise_items=not heartbeat, deleted_jobs=deleted_jobs)
                # A heartbeat follows a full pass over the host's jobs, so absent jobs are gone.
                reconciled = self.attention.close_refusals(
                    {**host, "jobs": list(host["jobs"].values()), "sessions": []}, complete=heartbeat) or reconciled
            if previous == self.by_host[host_name] and self.store.latest_sequence() == sequence and not reconciled:
                return
            self.version += 1
            self.changed.notify_all()
        if retry_deliveries:
            self.execution.retry_deliveries(host_name)
        self.schedule_triage()

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
        result = self.workspace.edit_registry(change)
        self.registry = self.workspace.registry()
        return result

    def repository_remotes(self, host: str, directories: list[str]) -> dict[str, list[str]]:
        return transport.repository_remotes(next(known for known in self.hosts if known.name == host), directories)

    def live_jobs(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Each host's job summaries as last streamed, by (host, job id)."""
        with self.changed:
            return {(host, job["id"]): job for host, entry in self.by_host.items() for job in entry["jobs"].values()}

    def document(self) -> dict[str, Any]:
        projects_error = self.refresh_registry()
        capacity_error = self.refresh_capacity()
        registry = self.registry
        with self.changed:
            document = self.with_attention({"time": time.time(), "build": BUILD, "project_labels": self.project_labels,
                    "projects": [{"id": project_id, **entry} for project_id, entry in registry_config(registry).items()],
                    "projects_error": projects_error, "hosts": [
                {**{key: value for key, value in self.by_host[host.name].items() if key not in ("jobs", "sessions") and not key.startswith("_")},
                 "jobs": [annotate(self.workspace, resolve(registry, host.name, stale_work(self.by_host[host.name], job))) for job in
                          sorted(self.by_host[host.name]["jobs"].values(), key=lambda job: job.get("created_at") or 0)],
                 "sessions": [annotate(self.workspace, resolve(registry, host.name, stale_work(self.by_host[host.name], session))) for session in
                              sorted(self.by_host[host.name]["sessions"].values(),
                                     key=lambda session: session.get("started_at") or 0)]}
                for host in self.hosts]})
        document = self.with_building(self.with_work(document), registry)
        document["building"]["capacity_error"] = capacity_error
        document["pipelines"] = self.pipelines(registry, self.by_host)
        return document

    def pipeline_updates(self, after: int) -> list[dict[str, Any]]:
        return self.pipelines(self.registry, self.by_host, after)

    def host_names(self) -> list[str]:
        return [host.name for host in self.hosts]

    def move_on_host(self, host_name: str, identity: str, label: str) -> None:
        host = next(host for host in self.hosts if host.name == host_name)
        moved = transport.call(host, ["mv", identity, label], timeout=30)

        def relabel(state: dict[str, Any]) -> None:   # shown at once, before fleetd's stream reports it
            for kind in ("jobs", "sessions"):
                if moved["id"] in state[kind]:
                    state[kind][moved["id"]]["project"] = label
        self.update(host_name, relabel)

    def read_document(self, host_name: str, job_id: str, document_id: str) -> dict[str, Any]:
        host = next(host for host in self.hosts if host.name == host_name)
        return fetch_document(host, job_id, document_id)

    def read_asset(self, host_name: str, job_id: str, document_id: str, asset_path: str) -> tuple[str, bytes]:
        host = next(host for host in self.hosts if host.name == host_name)
        return fetch_asset(host, job_id, document_id, asset_path)


def stale_work(host: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    stale = not host["ok"] or item.get("stale", False)
    return {**item, "stale": stale, "stale_reason":
            (host.get("error") or "awaiting the host’s complete snapshot") if stale else None}


def follow_host(state: FleetState, host: Host) -> None:
    """Keep one `fleetd stream` running for the host, reconnecting when it dies or goes quiet."""
    while True:
        error = run_stream(state, host)

        def mark_down(entry: dict[str, Any]) -> None:
            entry.update(ok=False, error=error, _syncing=False)

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
    # Both pipes are drained on their own threads. Applying a message may call the host again over the same ssh
    # master (the catch-up after hello); the master writes the stream into these pipes and, once one is full,
    # blocks every channel to the host, the catch-up's included, so reading here alone deadlocked.
    lines: queue.Queue[bytes | None] = queue.Queue()
    stderr_lines: list[str] = []

    def pump_stdout() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    def pump_stderr() -> None:
        for line in process.stderr:
            stderr_lines.append(line.decode(errors="replace").rstrip())
            del stderr_lines[:-20]

    pumps = [threading.Thread(target=pump, daemon=True) for pump in (pump_stdout, pump_stderr)]
    for pump in pumps:
        pump.start()
    try:
        while True:
            try:
                line = lines.get(timeout=STREAM_SILENCE_LIMIT)
            except queue.Empty:
                return f"no heartbeat for {STREAM_SILENCE_LIMIT}s"
            if line is None:
                process.wait(timeout=5)
                pumps[1].join(timeout=5)
                return stderr_lines[-1] if stderr_lines else f"stream ended (exit {process.returncode})"
            if line.strip():
                apply_message(state, host, json.loads(line))
    finally:
        if process.poll() is None:
            process.kill()


def apply_message(state: FleetState, host: Host, message: dict[str, Any]) -> None:
    kind = message.get("type")
    if kind == "input_observation":
        observation = InputObservation(**{key: message[key] for key in InputObservation.__dataclass_fields__
                                          if key in message})
        project = resolve(state.registry, host.name, {"project": observation.project})
        state.attention.observe_input(host.name, observation, project_id=project.get("project_id"))
        state.bump()
        return
    if kind == "hello":
        def hello(entry):
            for group in ("jobs", "sessions"):
                for item in entry[group].values():
                    item.update(stale=True, stale_since=entry.get("down_since", time.time()))
            entry.update(ok=True, error=None, _syncing=True, _seen_jobs=set(), _seen_sessions=set())
        state.update(host.name, hello, ingest=False)
        try:
            jobs = transport.catch_up_jobs(host)
            def catch_jobs(entry):
                entry["jobs"].update({job["id"]: job for job in jobs})
                entry["_seen_jobs"].update(job["id"] for job in jobs)
            state.update(host.name, catch_jobs,
                         subjects={f"job:{host.name}:{job['id']}" for job in jobs})
            for job in jobs:
                state.keep_documents(host.name, job)
        except (FleetError, ValueError, KeyError, OSError) as error:
            print(f"fleet: catch-up on {host.name} failed: {error}", file=sys.stderr)
        known = next((entry for entry in state.execution.hosts() if entry["name"] == host.name), None)
        lower = datetime.now(timezone.utc) - timedelta(days=30)
        if known and known["last_observed"]:
            lower = max(lower, datetime.fromisoformat(known["last_observed"]))
        try:
            sessions = transport.catch_up_sessions(host, lower.isoformat())
            for session in sessions:
                state.execution.observe_session(host.name, session, resolve(state.registry, host.name, session)["project_id"])
            def catch_sessions(entry):
                current = {session["id"]: session for session in sessions if session["status"] != "stopped"}
                entry["sessions"].update(current)
                entry["_seen_sessions"].update(current)
            state.update(host.name, catch_sessions, subjects={f"session:{host.name}:{session['id']}" for session in sessions})
        except (FleetError, ValueError, KeyError, OSError) as error:
            print(f"fleet: session catch-up on {host.name} failed: {error}", file=sys.stderr)
    elif kind == "job":
        job = message["job"]
        def report_job(entry):
            entry["jobs"][job["id"]] = job
            if entry.get("_syncing"):
                entry["_seen_jobs"].add(job["id"])
        state.update(host.name, report_job,
                     subjects={f"job:{host.name}:{job['id']}"})
        state.keep_documents(host.name, job)
    elif kind == "removed":
        if message.get("reason") == "removed by fleet rm":
            state.execution.removed(host.name, message["id"], at=message.get("removed_at"))
        state.update(host.name, lambda entry: entry["jobs"].pop(message["id"], None),
                     subjects={f"job:{host.name}:{message['id']}"},
                     deleted_jobs={f"job:{host.name}:{message['id']}"} if message.get("reason") in ("deleted", "removed by fleet rm") else frozenset())
    elif kind == "session":
        session = message["session"]
        def report_session(entry):
            entry["sessions"][session["id"]] = session
            if entry.get("_syncing"):
                entry["_seen_sessions"].add(session["id"])
        state.update(host.name, report_session,
                     subjects={f"session:{host.name}:{session['id']}"})
    elif kind == "session_removed":
        state.execution.stop_session(host.name, message["id"])
        state.update(host.name, lambda entry: entry["sessions"].pop(message["id"], None),
                     subjects={f"session:{host.name}:{message['id']}"})
    elif kind == "heartbeat":
        def heartbeat(entry):
            if entry.get("_syncing"):
                for identity in set(entry["sessions"]) - entry["_seen_sessions"]:
                    state.execution.stop_session(host.name, identity)
                for group in ("jobs", "sessions"):
                    entry[group] = {key: item for key, item in entry[group].items() if key in entry[f"_seen_{group}"]}
                entry["_syncing"] = False
        state.update(host.name, heartbeat, heartbeat=True)
    elif kind == "pipeline" and isinstance(message.get("pipeline"), str):
        state.report_pipeline(host.name, message["pipeline"], message.get("run"), message.get("baseline"))
    elif kind == "error":
        state.update(host.name, lambda entry: entry.update(ok=False, error=message.get("error"), _syncing=False))


def refusal_detail(item) -> dict[str, Any]:
    context = item.stream_context
    return {"host": context.host, "job": context.owner_id, "step": context.step, "state": item.state,
            "resolution": item.resolution_details, "rules": refusal_rules(item.refusals),
            "requests": [{"tool": refusal.tool, "description": refusal.description, "detail": refusal.detail,
                          "rules": None if refusal.rules is None else list(refusal.rules),
                          "denied_by": list(refusal.denied_by)}
                         for refusal in item.refusals]}


def question_detail(item, projects: dict[str, Any]) -> dict[str, Any]:
    """A session's question, where it waits, and that only its terminal can answer it."""
    context = item.stream_context
    project = projects.get(context.project_id) if context.project_id is not None else None
    return {"host": context.host, "session": context.owner_id, "cwd": context.cwd,
            "project": project.name if project is not None else None, "label": context.project,
            "state": item.state, "resolution": item.resolution_details,
            "questions": [asdict(question) for question in item.questions]}


def blocked_detail(item) -> dict[str, Any]:
    """A blocked job step and its final message; message is None when the host's fleetd reported none."""
    context = item.stream_context
    return {"host": context.host, "job": context.owner_id, "step": context.step, "message": context.message,
            "state": item.state, "resolution": item.resolution_details}


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
            elif path == "/api/history/runs" or path.startswith("/api/runs/"):
                self.run_history(path)
            elif path == "/api/doc":
                self.document()
            elif path == "/api/doc/asset":
                self.asset("host", "job")
            elif path == "/api/library/asset":
                self.asset("project")
            elif path == "/api/library":
                try:
                    documents = library.list()
                except ValueError as error:
                    self.respond(400, "application/json", json.dumps({"error": str(error)}).encode())
                    return
                for document in documents:
                    document["project_id"] = state.library_project_id(document["project"])
                self.respond(200, "application/json", json.dumps(
                    {"documents": documents, "projects": state.library_projects()}).encode())
            elif path == "/api/library/doc":
                self.library_document()
            elif path == "/api/library/overview":
                try:
                    projects = state.library_overview(library)
                except ValueError as error:
                    self.respond(400, "application/json", json.dumps({"error": str(error)}).encode())
                    return
                self.respond(200, "application/json", json.dumps({"projects": projects}).encode())
            elif path in ("/api/library/job", "/api/library/working"):
                self.stored_document(path.rsplit("/", 1)[1])
            elif path == "/api/move-in":
                self.move_in_options()
            elif path == "/api/state":
                self.respond(200, "application/json", json.dumps(state.document()).encode())
            elif path == "/api/decision":
                query = parse_qs(urlsplit(self.path).query)
                if "id" not in query:
                    self.error(400, "the item's id is required")
                    return
                try:
                    item = state.attention.get(query["id"][0])
                    proposal = open_decisions(state.store).proposal_for_attention(
                        item.source, item.source_reference)
                    detail = {"id": item.id, "question": item.headline,
                              "context": item.context_reference, "options": item.options,
                              "proposal": asdict(proposal) if proposal is not None else None,
                              # a job step's refused requests, answered with actions rather than words
                              "refusals": refusal_detail(item) if item.refusals else None,
                              "session_question": (question_detail(item, state.known_projects())
                                                   if item.questions or item.at_terminal else None),
                              # a blocked job step's question, answered by adding a step to the job
                              "blocked": (blocked_detail(item) if item.stream_context is not None
                                          and item.stream_context.blocked_step else None)}
                except LookupError as error:
                    self.error(404, str(error))
                    return
                self.respond(200, "application/json", json.dumps(detail, default=str).encode())
            elif path == "/api/bench":
                query = parse_qs(urlsplit(self.path).query)
                if "project" not in query:
                    self.error(400, "project is required")
                    return
                projection = project_status(query["project"][0], open_work(state.store), state.attention,
                    open_execution(state.store), open_library(state.store), open_decisions(state.store))
                try:
                    result = (bench_state(projection, query["slice"][0]) if "slice" in query
                              else bench_rooms(projection, state.live_jobs()))
                except ValueError as error:
                    self.error(404, str(error))
                    return
                self.respond(200, "application/json", json.dumps(result).encode())
            elif path == "/api/guidance":
                self.guidance()
            elif path == "/api/decisions":
                self.decisions()
            elif path == "/api/history":
                self.history()
            elif path in ("/", "/index.html"):
                self.respond(200, "text/html; charset=utf-8", index_page)
            elif path in PROTOTYPES:
                self.static_file(PROTOTYPES[path])
            elif path in app_files:
                self.respond(200, STATIC_TYPES.get(Path(path).suffix, "application/octet-stream"), app_files[path])
            elif path.startswith(STATIC_PREFIXES):
                self.static_file(path)
            elif path.startswith(tuple(CHECKOUT_FOLDERS)):
                self.checkout_file(path)
            else:
                self.respond(404, "text/plain", b"not found")

        def run_history(self, path: str) -> None:
            services = facades(state.store)
            try:
                if path == "/api/history/runs":
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    allowed = {"project", "work_item", "descendants", "host", "status", "kind", "unlinked", "since", "until", "limit"}
                    if query.keys() - allowed or any(len(values) != 1 for values in query.values()):
                        raise ValueError("unknown or repeated history filter")
                    filters = {key: values[0] for key, values in query.items()}
                    for key in ("unlinked", "descendants"):
                        if key in filters:
                            if filters[key] not in ("true", "false"):
                                raise ValueError(f"{key} must be true or false")
                            filters[key] = filters[key] == "true"
                    if "limit" in filters:
                        filters["limit"] = int(filters["limit"])
                    result = history_runs(services.execution, services.work, services.workspace, **filters)
                    jobs_cache = {}
                    for run in result["runs"]:
                        run["document_count"] = len(state.documents.run_documents(run, jobs_cache=jobs_cache))
                else:
                    identity = path.removeprefix("/api/runs/")
                    if not identity or "/" in identity:
                        raise LookupError("a stored run ID is required")
                    result = run_detail(identity, services.execution, services.work, services.library)
                    result["kept_documents"] = state.documents.run_documents(result["run"])
            except LookupError as error:
                self.error(404, str(error))
                return
            except (ValueError, FleetError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(result).encode())

        def do_POST(self) -> None:  # noqa: N802 — http.server naming
            path = self.path.split("?", 1)[0]
            action = path.removeprefix("/api/attention/") if path.startswith("/api/attention/") else None
            if (path not in FLOOR_CHANGES + GUIDANCE_CHANGES + ("/api/focus", "/api/decision/answer", "/api/agent/move")
                    and action not in ATTENTION_ACTIONS + REFUSAL_ACTIONS + JOB_ACTIONS):
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
                if path == "/api/decision/answer":
                    self.answer(body)
                elif action in REFUSAL_ACTIONS:
                    self.refusals(action, body)
                elif action in JOB_ACTIONS:
                    self.answer_blocked(body)
                elif action:
                    self.attention(action, body)
                elif path in ("/api/move-in", "/api/link"):
                    self.move_in(path.removeprefix("/api/"), body)
                elif path == "/api/merge":
                    self.merge(body)
                elif path == "/api/agent/move":
                    self.move_agent(body)
                elif path in GUIDANCE_CHANGES:
                    self.change_guidance(path, body)
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

        def guidance(self) -> None:
            """GET /api/guidance?project=&epic=&version= — the constitution, or the epic's charter, rendered with its
            history; an older version with version=N."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("project") or not query.get("version", "1").isdecimal():
                self.error(400, "project is required, and version is a number")
                return
            number = int(query["version"]) if "version" in query else None
            self.guidance_result(lambda services: guidance_view(services, query["project"], query.get("epic"), number))

        def decisions(self) -> None:
            """GET /api/decisions?project= or ?epic= — newest first; epic lists include charter promotion state."""
            query = parse_qs(urlsplit(self.path).query)
            epic = (query.get('epic') or [''])[0]
            project = (query.get('project') or [''])[0]
            if bool(epic) == bool(project):
                self.error(400, "exactly one of project or epic is required")
                return
            self.guidance_result(lambda services: epic_decisions(services, epic) if epic else project_decisions(services, project))

        def history(self) -> None:
            """GET /api/history?subject=&since= — the subject's audit trail, newest first, as `fleet history --json`
            prints it; subject takes an id, a unique id prefix or a subject such as attention:<id>."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("subject", "").strip():
                self.error(400, "subject is required")
                return
            try:
                since = parse_since(query["since"]) if query.get("since") else None
            except ValueError as error:
                self.error(400, str(error))
                return
            try:
                result = subject_history(state.store, query["subject"], since)
            except LookupError as error:
                self.error(404, str(error))
                return
            self.respond(200, "application/json", json.dumps(result).encode())

        def change_guidance(self, path: str, body: dict[str, Any]) -> None:
            """POST /api/guidance {"project", "epic" (null for the constitution), "markdown", "base": the version the
            editor opened, 0 for none} — a new version by web-user; refused with 409 if another version came first.
            POST /api/guidance/promote {"epic", "decision"} — the decision added to the charter's decisions in force."""
            if path == "/api/guidance/promote":
                if not isinstance(body.get("epic"), str) or not isinstance(body.get("decision"), str):
                    self.error(400, "epic and decision are required")
                    return

                def promote(services):
                    promote_decision(services, body["epic"], body["decision"], actor="web-user")
                    return guidance_view(services, services.work.get(body["epic"]).project, body["epic"])
                self.guidance_result(promote)
                return
            epic = body.get("epic")
            if (not isinstance(body.get("project"), str) or not isinstance(body.get("markdown"), str)
                    or not isinstance(epic, (str, type(None))) or type(body.get("base")) is not int):
                self.error(400, "project, markdown and base (a version number) are required")
                return

            def write(services):
                services.records.write_guidance(body["project"], body["markdown"], epic=epic, actor="web-user",
                                                base=body["base"])
                return guidance_view(services, body["project"], epic)
            self.guidance_result(write)

        def guidance_result(self, produce: Callable[[Any], dict[str, Any]]) -> None:
            try:
                result = produce(facades(state.store))
            except GuidanceConflict as error:
                self.error(409, str(error))
            except LookupError as error:
                self.error(404, str(error))
            except ValueError as error:
                self.error(400, str(error))
            else:
                self.respond(200, "application/json", json.dumps(result, default=str).encode())

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
            self.respond(200, "application/json", json.dumps(asdict(state.workspace.focus_snapshot())).encode())

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

        def move_agent(self, body: dict[str, Any]) -> None:
            """POST /api/agent/move {"host": host, "id": job or session id, "project": ID} — move it to that project."""
            if not all(isinstance(body.get(key), str) for key in ("host", "id", "project")):
                self.error(400, "host, id and project are required")
                return
            self.change_floors(lambda: state.move_agent(body["host"], body["id"], body["project"]))

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

        def answer(self, body: dict[str, Any]) -> None:
            if not isinstance(body.get("id"), str) or not isinstance(body.get("answer"), str):
                self.error(400, "item id and answer are required")
                return
            try:
                decision = open_decisions(state.store).answer(body["id"], body["answer"], actor="user")
            except LookupError as error:
                self.error(404, str(error))
                return
            except (FleetError, ValueError) as error:
                self.error(400, str(error))
                return
            state.bump()
            self.respond(200, "application/json", json.dumps(asdict(decision), default=str).encode())

        def refusals(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/attention/allow {"id": item id, "scope": "refused" | "bash"} — add permission rules to
            the job on its worker and continue the refused step there.
            POST /api/attention/dismiss {"id": item id} — resolve the batch and change nothing."""
            if not isinstance(body.get("id"), str) or (action == "allow" and not isinstance(body.get("scope"), str)):
                self.error(400, "the item's id is required" + (", and a scope" if action == "allow" else ""))
                return
            try:
                if action == "allow":
                    details = open_execution(state.store).grant_permissions(body["id"], body["scope"], actor="web-user")
                else:
                    details = state.attention.dismiss_refusals(body["id"], actor="web-user").resolution_details
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except ItemResolved as error:
                self.error(409, str(error))
                return
            except (FleetError, ValueError, RuntimeError) as error:
                self.error(400, str(error))
                return
            state.bump()
            self.respond(200, "application/json", json.dumps({"id": body["id"], "resolution": details}).encode())

        def answer_blocked(self, body: dict[str, Any]) -> None:
            """POST /api/attention/answer {"id": item id, "answer": reply, "work_item"?: id} — add the reply as a
            step to the blocked job on its worker, serving work_item or else the blocked step's work, and resolve
            the item."""
            if not isinstance(body.get("id"), str) or not isinstance(body.get("answer"), str):
                self.error(400, "the item's id and an answer are required")
                return
            if not isinstance(body.get("work_item"), (str, type(None))):
                self.error(400, "work_item must be a work item ID")
                return
            try:
                details = open_execution(state.store).answer_blocked(body["id"], body["answer"], actor="web-user",
                                                                     work_item=body.get("work_item"))
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except ItemResolved as error:
                self.error(409, str(error))
                return
            except (FleetError, ValueError, RuntimeError) as error:
                self.error(400, str(error))
                return
            state.bump()
            self.respond(200, "application/json", json.dumps({"id": body["id"], "resolution": details}).encode())

        def attention(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/attention/acknowledge|snooze|reopen|resolve {"id": item id, "seconds": snooze length}"""
            if not isinstance(body.get("id"), str):
                self.error(400, "the item's id is required")
                return
            try:
                result = state.act_on_attention(action, body["id"], body.get("seconds"), body.get("undo"))
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except ItemResolved as error:
                self.error(409, str(error))
                return
            except (FleetError, ValueError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps({"id": body["id"], "action": action, **result}).encode())

        def static_file(self, path: str) -> None:
            """Vendored libraries and 3D assets; anything resolving outside those folders is refused."""
            target = (WEB_ROOT / unquote(path).lstrip("/")).resolve()
            allowed = any(target.is_relative_to(WEB_ROOT / prefix.strip("/")) for prefix in STATIC_PREFIXES)
            if not allowed or not target.is_file():
                self.respond(404, "text/plain", b"not found")
                return
            content_type = STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self.respond(200, content_type, target.read_bytes(), cache_seconds=3600)

        def checkout_file(self, path: str) -> None:
            """Art and concept files from a source checkout, for the prototypes; nothing outside those folders."""
            prefix = next(p for p in CHECKOUT_FOLDERS if path.startswith(p))
            folder = CHECKOUT_FOLDERS[prefix].resolve()
            target = (folder / unquote(path.removeprefix(prefix))).resolve()
            if not target.is_relative_to(folder) or not target.is_file():
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

        def asset(self, *owner: str) -> None:
            """GET /api/doc/asset?host=&job=&id=&path= or /api/library/asset?project=&id=&path= — an image
            a document links to, resolved beside the document under the document's own roots."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not all(query.get(key) for key in (*owner, "id", "path")) or (
                    "host" in owner and query["host"] not in state.host_names()):
                self.error(400, f"{', '.join(owner)}, id and path are required")
                return
            try:
                if "host" in owner:
                    found = state.read_asset(query["host"], query["job"], query["id"], query["path"])
                else:
                    found = library.read_asset(query["project"], query["id"], query["path"])
            except DocumentAccessDenied as error:
                self.error(403, str(error))
                return
            except AssetNotImage as error:
                self.error(415, str(error))
                return
            except AssetTooLarge as error:
                self.error(413, str(error))
                return
            except FleetError as error:
                self.error(404, str(error))
                return
            if found is None:
                self.error(404, "image not found")
                return
            content_type, content = found
            # an SVG can carry script: the policy keeps it inert even if opened on its own
            self.respond(200, content_type, content, headers={"Content-Security-Policy": ASSET_POLICY,
                                                              "X-Content-Type-Options": "nosniff"})

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

        def stored_document(self, section: str) -> None:
            """GET /api/library/job?project=&job=&id= or /api/library/working?project=&id= — from the project's store,
            so it reads whether or not the job is still on its host."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("project") or not query.get("id") or (section == "job" and not query.get("job")):
                self.respond(400, "application/json", b'{"error": "project, id and (for a job) job are required"}')
                return
            body = (state.documents.read(query["project"], query["job"], query["id"]) if section == "job"
                    else state.documents.read_working(query["project"], query["id"]))
            if body is None:
                self.respond(404, "application/json", b'{"error": "document not in the project store"}')
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

        def respond(self, status: int, content_type: str, body: bytes, *, cache_seconds: int = 0,
                    headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Cache-Control", f"max-age={cache_seconds}" if cache_seconds else "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *arguments: Any) -> None:  # noqa: A002 — silence access log
            pass

    return Handler


def serve(hosts: list[Host], *, port: int, bind: str, open_browser: bool = False,
          libraries: dict[str, Any] | None = None, project_labels: dict[str, str] | None = None,
          pipelines: dict[str, dict[str, str]] | None = None) -> None:
    state = FleetState(hosts, project_labels, pipelines=pipelines)
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
