"""What the deck writes and derives on top of host state, shared by live and fixture decks.

A state class using LiveWorkspace provides `changed` (a Condition), `version`,
`workspace` (a WorkspaceFacade), `attention` (an AttentionFacade), `registry` (the project
Registry in use), `project_labels`, `capacity`, `known_projects()`, `host_names()`,
`edit_registry()` and `repository_remotes()`.
"""
from __future__ import annotations

from fleet.projections.live import live_document, building_document
from fleet.projections.attention import attention_items

import json
from copy import copy
import fcntl
from pathlib import Path
import logging
import pickle
import sys
from dataclasses import asdict
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Container, TypeVar

from fleet.modules.attention import AttentionFacade, InputObservation
from fleet.modules.workspace import Registry, WorkspaceFacade
from fleet.errors import FleetError
from fleet.services.projects import LiveProjects
from fleet.services.attention import LiveAttention
from fleet.projections.live import LiveProjection
from fleet.projections.documents import LibraryProjection
from fleet.transport import Host
from fleet.projections.workspace import resolve

T = TypeVar("T")


class LiveWorkspace(LiveProjects, LiveAttention, LiveProjection, LibraryProjection):
    changed: threading.Condition
    version: int
    workspace: WorkspaceFacade
    attention: AttentionFacade
    woken_until: float
    registry: Registry
    project_labels: dict[str, str]
    capacity: int
    pipeline_config: dict[str, dict[str, str]]           # name → {"host", "project": room label}, as configured
    pipeline_runs: dict[tuple[str, str], dict[str, Any]]  # (host, name) → {"run", "baseline", "seq"} as last reported
    work_links: tuple[object, dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], str]] | None = None  # revision, links, run ids
    pipeline_seq: int
    documents: Any   # each project's injected document store

    def known_projects(self) -> Container[str]:
        raise NotImplementedError

    def host_names(self) -> list[str]:
        raise NotImplementedError

    def edit_registry(self, change: Callable[[Registry], T]) -> T:
        """Apply `change` to the registry as stored, keep the result as `registry`, and return what it returned."""
        raise NotImplementedError

    def repository_remotes(self, host: str, directories: list[str]) -> dict[str, list[str]]:
        """Remote URLs of each directory on the host (see transport.repository_remotes)."""
        raise NotImplementedError


    def move_on_host(self, host: str, identity: str, label: str) -> None:
        """Give a job or session on the host a new project label (fleetd `mv`)."""
        raise NotImplementedError


    def with_building(self, document: dict[str, Any], registry: Registry) -> dict[str, Any]:
        self.workspace.settle()
        return building_document(self, document, registry)

    def bump(self) -> None:
        """Push a new document to every browser."""
        with self.changed:
            self.version += 1
            self.changed.notify_all()


    def triage_statuses(self, items: list[dict]) -> dict[str, dict]:
        """Compute scheduler reads in the service layer before projecting a document."""
        projects = {item["project_id"] for item in items if item["project_id"]}
        statuses = {project: self.triage_status(project) for project in projects}
        for status in statuses.values():
            if status["live_run"]:
                run = self.execution.get_run(status["live_run"]["id"])
                status["live_run"].update(host=run.host, remote_job_id=run.remote_job_id)
        return statuses

    def focus_snapshot(self):
        return asdict(self.workspace.focus_snapshot())

    def document(self) -> dict[str, Any]:
        raise NotImplementedError

    def job_hosts(self) -> dict[str, tuple[bool, set[str]]]:
        """Each followed host: whether it is reachable now and the ids of the jobs it lists."""
        raise NotImplementedError


    def clock(self) -> float:
        """Now, for judging how recent work is; a recorded fixture answers with its own time."""
        return time.time()


    def report_pipeline(self, host: str, name: str, run: dict[str, Any] | None,
                        baseline: dict[str, Any] | None) -> None:
        """A host's latest summary of a pipeline's run; browsers get it as a pipeline event, not a new document."""
        with self.changed:
            self.pipeline_seq += 1
            self.pipeline_runs[(host, name)] = {"run": run, "baseline": baseline, "seq": self.pipeline_seq}
            self.changed.notify_all()


    def wait_for_change(self, seen_version: int, timeout: float, seen_pipelines: int | None = None) -> int:
        """Also wakes when a snooze ends, so the item comes back on every deck without a reload, and when a pipeline
        report arrives if `seen_pipelines` is given (compare `pipeline_seq` to tell)."""
        now = self.attention.clock().timestamp()
        ending = self.attention.next_snooze_after(self.woken_until)
        wait = timeout if ending is None else max(0.0, min(timeout, ending - now))
        with self.changed:
            self.changed.wait_for(lambda: self.version != seen_version or (
                seen_pipelines is not None and self.pipeline_seq != seen_pipelines), timeout=wait)
            if self.version == seen_version and ending is not None and self.attention.clock().timestamp() >= ending:
                self.woken_until = max(self.woken_until, ending)
                self.version += 1
            return self.version

EVENTS_PER_JOB = "15"
STREAM_SILENCE_LIMIT = 20
RECONNECT_DELAY = 3

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
                 *, container) -> None:
        self.hosts = hosts
        self.project_labels = project_labels or {}
        self.container = container
        self.responder = container.responder_worker()
        self.transport = container.transport()
        self.store = container.store()
        self.observed_runs: dict = {}  # successful job ingestion, including persisted link context
        self.indexed: dict = {}   # library entries as last indexed (see observe_runs)
        self.taken_decisions: set = set()   # streamed decision ids already handled (see record_decisions)
        self.workspace = workspace if workspace is not None else container.initialized_workspace(actor="web-user")
        self.load_registry = load_registry or self.workspace.registry
        self.registry = self.load_registry()
        self.load_capacity = load_capacity or self.workspace.capacity
        self.capacity = self.load_capacity()
        self.attention = container.initialized_attention()
        self.execution = container.execution()
        self.run_library = container.library()
        self.decisions = container.decisions()
        self.records = container.records()
        self.reads = container.live_readers(workspace=self.workspace, attention=self.attention)
        scheduler = container.triage_scheduler(deliver=container.deliver_triage,
                                               host=lambda name: self.transport.host_by_name(name))
        self.triage_status = scheduler.status
        self.schedule = scheduler.schedule
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
        self.documents = container.project_documents()
        self.trace_retainer = self.transport.keep_run_trace
        self.keeper = container.document_keeper(self.documents, self.fetch_raw, keep_trace=self.keep_trace)
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

    def job_documents(self, host, job):
        with self.changed:
            return snapshot(self.by_host[host]['jobs'][job].get('documents', []))

    def fetch_raw(self, host_name: str, job_id: str, document_id: str) -> dict[str, Any]:
        """A job document's Markdown as its host serves it, before rendering."""
        host = next(host for host in self.hosts if host.name == host_name)
        return self.transport.call(host, ["read", job_id, document_id], timeout=30)

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
        bodies = {intent['id']: json.dumps(asdict(self.decisions.get(intent['key'])), default=str)
                  for intent in self.records.pending_intents()
                  if intent['path'] == f"decisions/{intent['key']}.json"}
        try:
            self.records.reconcile(bodies)
        except OSError:
            logging.getLogger(__name__).exception('Records publication remains pending')
        self.schedule()

    def follow_history(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.schedule_triage()
            changes = self.store.history_after(self.history_cursor)
            if changes:
                self.history_cursor = int(changes[-1]["sequence"])
                self.bump()
            if hasattr(self, "_runtime_worker_success"):
                self._runtime_worker_success("history-scheduler")
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
            retry_deliveries = self.by_host[host_name]["ok"]

            def public(value):
                return {key: item for key, item in value.items() if not key.startswith("_")}

            context_only = public(previous) == public(self.by_host[host_name])
            reconciled = False
            if ingest:
                entry = self.by_host[host_name]
                host = {**entry, **{kind: {identity: item for identity, item in entry[kind].items()
                                          if not item.get("stale")} for kind in ("jobs", "sessions")}}
                self.container.observe_runs(host, self.indexed,
                             lambda job: resolve(self.registry, host_name, job)["project_id"],
                             observed=self.observed_runs)
                self.container.observe_sessions(host, lambda session: resolve(self.registry, host_name, session)["project_id"])
                self.container.record_decisions(host,
                                 lambda job: resolve(self.registry, host_name, job)["project_id"], self.taken_decisions)
                reconciled = self.attention.observe({**host,
                    "jobs": [resolve(self.registry, host_name, job) for job in host["jobs"].values()],
                    "sessions": [resolve(self.registry, host_name, session) for session in host["sessions"].values()]},
                    subjects=subjects, raise_items=not heartbeat, deleted_jobs=deleted_jobs)
                # A heartbeat follows a full pass over the host's jobs, so absent jobs are gone.
                reconciled = self.attention.close_refusals(
                    {**host, "jobs": list(host["jobs"].values()), "sessions": []}, complete=heartbeat) or reconciled
            if previous != self.by_host[host_name] or self.store.latest_sequence() != sequence or reconciled:
                self.version += 1
                self.changed.notify_all()
        if retry_deliveries:
            if context_only:
                self.execution.retry_decisions(host_name)
            else:
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
        return self.transport.repository_remotes(next(known for known in self.hosts if known.name == host), directories)

    def live_jobs(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Each host's job summaries as last streamed, by (host, job id)."""
        with self.changed:
            return {(host, job["id"]): job for host, entry in self.by_host.items() for job in entry["jobs"].values()}

    def snapshot_view(self) -> FleetState:
        """Capture the mutable observation inputs; project with a private lock.

        Store readers remain authoritative. Only this generation's host and
        pipeline inputs are copied, so a long projection cannot stop ingestion.
        """
        with self.changed:
            view = copy(self)
            view.changed = threading.Condition()
            view.by_host = snapshot(self.by_host)
            view.pipeline_runs = snapshot(self.pipeline_runs)
            return view

    def accept_snapshot_view(self, view: FleetState) -> None:
        """Retain refreshed read context used by subsequent host ingestion."""
        with self.changed:
            self.registry = view.registry
            self.capacity = view.capacity
            self.work_links = view.work_links

    def document(self) -> dict[str, Any]:
        projects_error = self.refresh_registry()
        capacity_error = self.refresh_capacity()
        with self.changed:
            items = attention_items(self.reads.attention, [self.by_host[host.name] for host in self.hosts])
            return live_document(self, projects_error, capacity_error, self.triage_statuses(items), items)

    def pipeline_updates(self, after: int) -> list[dict[str, Any]]:
        return self.pipelines(self.registry, self.by_host, after)

    def host_names(self) -> list[str]:
        return [host.name for host in self.hosts]

    def move_on_host(self, host_name: str, identity: str, label: str) -> None:
        host = next(host for host in self.hosts if host.name == host_name)
        moved = self.transport.call(host, ["mv", identity, label], timeout=30)

        def relabel(state: dict[str, Any]) -> None:   # shown at once, before fleetd's stream reports it
            for kind in ("jobs", "sessions"):
                if moved["id"] in state[kind]:
                    state[kind][moved["id"]]["project"] = label
        self.update(host_name, relabel)

    def read_document(self, host_name: str, job_id: str, document_id: str) -> dict[str, Any]:
        host = next(host for host in self.hosts if host.name == host_name)
        return {**self.container.read_document(host=host, job_id=job_id, document_id=document_id), "host": host.name}

    def read_asset(self, host_name: str, job_id: str, document_id: str, asset_path: str) -> tuple[str, bytes]:
        host = next(host for host in self.hosts if host.name == host_name)
        return self.container.read_asset(host=host, job_id=job_id, document_id=document_id, asset_path=asset_path)

def follow_host(state: FleetState, host: Host, stop: threading.Event | None = None) -> None:
    """Keep one `fleetd stream` running for the host, reconnecting when it dies or goes quiet."""
    while stop is None or not stop.is_set():
        error = run_stream(state, host, stop)
        if stop is not None and stop.is_set():
            return

        def mark_down(entry: dict[str, Any]) -> None:
            entry.update(ok=False, error=error, _syncing=False)

        state.update(host.name, mark_down)
        if stop is None:
            time.sleep(RECONNECT_DELAY)
        else:
            stop.wait(RECONNECT_DELAY)

def run_stream(state: FleetState, host: Host, stop: threading.Event | None = None) -> str:
    """Apply stream messages until the stream ends; return why it ended."""
    def receive(message):
        apply_message(state, host, message)
        if hasattr(state, '_runtime_worker_success'):
            state._runtime_worker_success('host:' + host.name)
    return state.transport.follow_stream(host, receive,
                                  events=EVENTS_PER_JOB, silence_limit=STREAM_SILENCE_LIMIT, stop=stop)

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
            jobs = state.transport.catch_up_jobs(host)
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
            sessions = state.transport.catch_up_sessions(host, lower.isoformat())
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


class LiveRuntime:
    """Own one state's live workers and wait for them to finish on close.

    A closed runtime stays closed; create a new state for a new live session.
    """

    def __init__(self, state) -> None:
        if getattr(state, "is_runtime_subscriber", False):
            raise FleetError("subscriber cannot own runtime; start fleet serve")
        self.state = state
        self.stop = threading.Event()
        self.lock = None
        self.errors = {}
        self.recoveries = {}
        state._runtime_worker_success = self.worker_recovered
        if hasattr(state, "container"):
            path = Path(state.container.settings()['store_path']).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.lock = open(str(path) + '.runtime.lock', 'a+')
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.lock.close()
                raise FleetError(f"runtime already owned for store {path}") from None
        self.threads = [self.worker('history-scheduler', state.follow_history, self.stop)]
        self.threads.extend(self.worker('host:' + host.name, follow_host, state, host, self.stop)
                            for host in state.hosts)
        self.responder = getattr(state, 'responder', None)
        if self.responder is not None:
            self.threads.append(self.worker('responder', self.responder.run, self.stop, self.worker_recovered))

    def worker(self, name, target, *args):
        def run():
            delay = 0.25
            while not self.stop.is_set():
                if name in self.errors:
                    self.recoveries[name]['restarts'] += 1
                    self.recoveries[name]['retrying'] = True
                started = time.monotonic()
                try:
                    target(*args)
                    if self.stop.is_set():
                        return
                    error = 'worker exited unexpectedly'
                except BaseException as failure:
                    error = f'{type(failure).__name__}: {failure}'
                    logging.getLogger(__name__).exception('Runtime worker %s failed; retrying', name)
                self.errors[name] = error
                previous = self.recoveries.get(name, {})
                self.recoveries[name] = {'last_error': error, 'failed_at': time.time(),
                                        'recovered_at': None, 'retrying': False, 'restarts': previous.get('restarts', 0)}
                if time.monotonic() - started >= 30:
                    delay = 0.25
                if self.stop.wait(delay):
                    return
                delay = min(delay * 2, 30)
        return threading.Thread(name=name, target=run, daemon=True)

    def worker_recovered(self, name):
        if name in self.errors:
            self.recoveries[name] = {**self.recoveries[name], 'recovered_at': time.time(), 'retrying': False}
            self.errors.pop(name, None)

    def health(self):
        workers = {thread.name: {'alive': thread.is_alive(), 'error': self.errors.get(thread.name),
                                **self.recoveries.get(thread.name, {})}
                   for thread in self.threads}
        if self.responder is not None:
            # Worker liveness and child liveness are distinct. An initializing or
            # dead child must never report a healthy responder.
            worker = workers['responder']
            worker['worker_alive'] = worker['alive']
            child = self.responder.health()
            worker.update(child)
            worker['error'] = self.errors.get('responder') or child.get('error')
            worker['alive'] = worker['worker_alive'] and child['alive']
        return {'healthy': all(worker['alive'] and worker.get('ready', True) and not worker['error']
                               for worker in workers.values()),
                'stopping': self.stop.is_set(), 'workers': workers}

    def close(self, timeout=5.0) -> None:
        self.stop.set()
        if self.responder is not None:
            self.responder.close()
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            if thread.ident is not None:
                thread.join(max(0.0, deadline - time.monotonic()))
        # Never allow a successor while old workers still write. Process exit releases the lock.
        if self.lock is not None and not any(thread.is_alive() for thread in self.threads):
            self.lock.close()
            self.lock = None


_start_lock = threading.Lock()


def start_live(state) -> LiveRuntime:
    """Start once per state and return the same owned runtime on repeated calls."""
    with _start_lock:
        runtime = getattr(state, "_live_runtime", None)
        if runtime is not None:
            return runtime
        runtime = LiveRuntime(state)
        try:
            for thread in runtime.threads:
                thread.start()
        except BaseException:
            runtime.stop.set()
            runtime.close()
            raise
        state._live_runtime = runtime
        return runtime
