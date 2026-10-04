"""What the deck writes and derives on top of host state, shared by live and fixture decks.

A state class using LiveWorkspace provides `changed` (a Condition), `version`,
`workspace` (a WorkspaceFacade), `attention` (an AttentionFacade), `registry` (the project
Registry in use), `project_labels`, `capacity`, `known_projects()`, `host_names()`,
`edit_registry()` and `repository_remotes()`.
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import asdict
import threading
import time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Container, TypeVar
from uuid import uuid4

from fleet.modules.attention import AttentionFacade
from fleet.projections.attention import attention_display
from fleet.modules.workspace import Registry, WorkspaceFacade, AlreadyHoused
from fleet.container import FleetError
T = TypeVar("T")


class LiveWorkspace:
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
    work_links: tuple[tuple[Any, int], dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], str]] | None = None  # revision, links, run ids
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

    def check_hosts(self, hosts: list[str], label: str) -> None:
        if not hosts or not label or any(host not in self.host_names() for host in hosts):
            raise FleetError("known hosts and a label are required")

    def move_in(self, hosts: list[str], label: str, shutter: str | None = None) -> dict[str, Any]:
        """Register an unregistered label on `hosts` as one project on the lowest free floor, named as its room is.
        When the building is full the only way in is to shutter a floor first (`shutter`); capacity never grows here."""
        self.check_hosts(hosts, label)
        result = self.workspace.move_in(hosts, label, shutter, self.project_labels.get(label))
        self.registry = self.workspace.registry()
        self.bump()
        return asdict(result)

    def link_in(self, project_id: str, hosts: list[str], label: str) -> dict[str, Any]:
        """Link the label on `hosts` to an existing project: its work joins that project, and no floor is taken."""
        self.check_hosts(hosts, label)
        if project_id not in self.known_projects():
            raise LookupError(f"no project '{project_id}'")
        result = self.workspace.link_in(project_id, hosts, label)
        for host in hosts:
            self.execution.assign_label(host, label, project_id, actor="web-user")
            self.documents.assign_label(host, label, project_id)
        self.registry = self.workspace.registry()
        self.bump()
        return asdict(result)

    def move_on_host(self, host: str, identity: str, label: str) -> None:
        """Give a job or session on the host a new project label (fleetd `mv`)."""
        raise NotImplementedError

    def move_agent(self, host: str, identity: str, project_id: str) -> dict[str, Any]:
        """Move a job, or a session started outside fleet, to a project: it takes the project's label on that host,
        and a host the project has no label on is linked under the project's first label."""
        if host not in self.host_names() or not identity:
            raise FleetError("a known host and an agent id are required")
        if project_id not in self.known_projects():
            raise LookupError(f"no project '{project_id}'")
        project = self.registry.get(project_id)
        label = next((link.label for link in project.links if link.host == host), None)
        linked = label is None
        if linked:
            label = project.links[0].label if project.links else project.name
            self.edit_registry(lambda registry: registry.link(project_id, host, label))
        self.move_on_host(host, identity, label)
        self.bump()
        return {"host": host, "id": identity, "project": label, "project_id": project_id, "linked": linked}

    def move_in_options(self, label: str, hosts: list[str]) -> dict[str, Any]:
        """What moving the label in on `hosts` could mean: projects it may belong to (see Registry.link_candidates),
        each with its floor or crate, and hosts whose repositories couldn't be read."""
        self.check_hosts(hosts, label)
        document = self.document()
        directories = {host["name"]: sorted({item["cwd"] for item in host["jobs"] + host["sessions"]
                                             if item.get("project") == label and item.get("cwd")})
                       for host in document["hosts"] if host["name"] in hosts}
        remotes, errors = [], []
        with ThreadPoolExecutor(max_workers=max(1, len(directories))) as pool:
            asked = {host: pool.submit(self.repository_remotes, host, found) for host, found in directories.items() if found}
            for host, future in asked.items():
                try:
                    remotes += [(host, label, url) for urls in future.result().values() for url in urls]
                except FleetError as error:
                    errors.append(str(error))
        building = document["building"]
        return {"label": label, "hosts": hosts, "errors": errors, "candidates": [
            {"project_id": candidate.project_id, "name": self.registry.get(candidate.project_id).name,
             "reasons": list(candidate.reasons), "floor": building["floors"].get(candidate.project_id),
             "shuttered": candidate.project_id in building["shuttered"]}
            for candidate in self.registry.link_candidates(label, hosts, remotes, self.project_labels.get(label))]}

    def merge(self, keep: str, other: str) -> dict[str, Any]:
        """Fold a project registered by mistake into the older one (Registry.merge) and free its floor."""
        for project_id in (keep, other):
            if project_id not in self.known_projects():
                raise LookupError(f"no project '{project_id}'")
        result = self.workspace.merge(keep, other)
        self.registry = self.workspace.registry()
        self.bump()
        return asdict(result)

    def shutter(self, project_id: str) -> dict[str, Any]:
        """Pack a project away in the storehouse (ADR 0005): its floor is freed; its ID, links and records stay."""
        if project_id not in self.known_projects():
            raise LookupError(f"no project '{project_id}'")
        result = self.workspace.shutter(project_id)
        self.bump()
        return asdict(result)

    def restore(self, project_id: str, shutter: str | None = None) -> dict[str, Any]:
        """Move a crate back in: to its old floor if free, else the lowest free one; when full, only by shuttering.
        A registered project with no floor moves in the same way."""
        if project_id not in self.known_projects():
            raise LookupError(f"no project '{project_id}'")
        result = self.workspace.restore(project_id, shutter)
        self.bump()
        return asdict(result)

    def with_building(self, document: dict[str, Any], registry: Registry) -> dict[str, Any]:
        """Add the floors registered projects occupy within capacity with each one's focus, the projects in the
        storehouse, and the live projects that have no floor. Project work is not sent with every update: the plan
        panel reads it from /api/bench when it is open."""
        self.workspace.settle()
        building = self.container.building_state(workspace=self.workspace, registry=registry, capacity=self.capacity)
        return {**document, "building": building,
                "attention_display": attention_display(document["attention"], building, document["projects"])}

    def bump(self) -> None:
        """Push a new document to every browser."""
        with self.changed:
            self.version += 1
            self.changed.notify_all()

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        self.workspace.set_focus(focus, projects, labels)
        self.bump()

    def document(self) -> dict[str, Any]:
        raise NotImplementedError

    def job_hosts(self) -> dict[str, tuple[bool, set[str]]]:
        """Each followed host: whether it is reachable now and the ids of the jobs it lists."""
        raise NotImplementedError

    def library_projects(self) -> list[dict[str, Any]]:
        """Every project with a document store: its jobs newest first, each saying whether it is still on its
        host, and its working documents. Stored documents stay readable whatever the host's state."""
        registry, hosts = self.registry, self.job_hosts()
        projects = []
        for project_id in self.documents.projects():
            jobs = self.stored_jobs(project_id, hosts)
            project = registry.projects.get(project_id)
            projects.append({"id": project_id, "name": project.name if project else None,
                             "jobs": jobs, "working": self.documents.working(project_id)})
        return projects

    def clock(self) -> float:
        """Now, for judging how recent work is; a recorded fixture answers with its own time."""
        return time.time()

    def library_overview(self, library: Any) -> list[dict[str, Any]]:
        """Each project's overview (see fleet.projections.overview): every project with a library root or a document store."""
        overview = self.__dict__.setdefault("overview", self.container.overview())
        registry, hosts = self.registry, self.job_hosts()
        attention = self.container.attention_items(attention=self.attention, hosts=[{"name": name, "ok": ok} for name, (ok, _) in hosts.items()])
        documents = library.list()
        projects: dict[str, dict[str, Any]] = {}
        for key in sorted(library.roots):
            project_id = self.library_project_id(key)
            projects.setdefault(project_id or "library:" + key, {"project_id": project_id, "library": key})
        for project_id in self.documents.projects():
            projects.setdefault(project_id, {"project_id": project_id, "library": None})
        result = []
        for entry in projects.values():
            project_id, key = entry["project_id"], entry["library"]
            project = registry.projects.get(project_id) if project_id else None
            jobs = self.stored_jobs(project_id, hosts) if project_id else []
            result.append(overview.build(
                name=project.name if project else key or project_id, project_id=project_id, library=key,
                root=library.root(key) if key else None,
                documents=[document for document in documents if document["project"] == key] if key else [],
                jobs=jobs, attention=attention, now=self.clock(),
                read_job=lambda job_key, document_id, project_id=project_id: self.documents.text(project_id, job_key, document_id)))
        return result

    def stored_jobs(self, project_id: str, hosts: dict[str, tuple[bool, set[str]]]) -> list[dict[str, Any]]:
        jobs = self.documents.jobs(project_id)
        for job in jobs:
            reachable, listed = hosts.get(job["host"], (False, set()))
            job["availability"] = ("on host" if reachable and job["id"] in listed
                                   else "gone from host" if reachable else "host offline")
        return jobs

    def library_project_id(self, key: str) -> str | None:
        """The project a `fleet library add` key names: its ID, its name, or a label linked to it on one project."""
        with contextlib.suppress(FleetError):
            return self.registry.resolve(key)
        owners = {project.id for project in self.registry.projects.values()
                  if any(link.label == key for link in project.links)}
        return owners.pop() if len(owners) == 1 else None

    def act_on_attention(self, action: str, item_id: str, seconds: float | None = None, undo: str | None = None) -> dict:
        result = {}
        if action == "delegate":
            self.attention.delegate(item_id, actor="web-user")
        elif action == "take":
            self.attention.take(item_id, actor="web-user")
        elif action == "acknowledge":
            self.attention.acknowledge(item_id, actor="web-user")
        elif action == "snooze":
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
                raise FleetError("snooze needs a positive number of seconds")
            self.attention.snooze(item_id, until=self.attention.clock() + timedelta(seconds=seconds), actor="web-user")
        elif action == "reopen":
            self.attention.reopen(item_id, actor="web-user")
        elif action == "resolve":
            with self.changed:
                now = time.monotonic()
                receipts = {key: value for key, value in getattr(self, "attention_undos", {}).items()
                            if value[0] > now}
                previous, resolved = self.attention.resolve_undoable(item_id, actor="web-user")
                token = str(uuid4())
                receipts[token] = (now + 6, previous, resolved)
                self.attention_undos = receipts
                result = {"undo": token, "undo_seconds": 6}
        elif action == "undo-resolve":
            with self.changed:
                receipts = getattr(self, "attention_undos", {})
                receipt = receipts.get(undo) if isinstance(undo, str) else None
                if receipt is None or receipt[0] <= time.monotonic() or receipt[1].id != item_id:
                    raise FleetError("Resolve Undo expired or is unavailable")
                self.attention.undo_resolution(receipt[1], receipt[2], actor="web-user")
                del receipts[undo]
        else:
            raise FleetError(f"unknown attention action '{action}'")
        self.bump()
        return result

    def with_attention(self, document: dict[str, Any]) -> dict[str, Any]:
        """Add stored focus choices and the Attention projection."""
        services = self.container.services()
        items = self.container.attention_items(attention=self.attention, hosts=document["hosts"])
        triage = {project: self.container.triage_scheduler(deliver=None, host=None).status(project)
                  for project in {item["project_id"] for item in items if item["project_id"]}}
        for status in triage.values():
            if status["live_run"]:
                run = services.execution.get_run(status["live_run"]["id"])
                status["live_run"].update(host=run.host, remote_job_id=run.remote_job_id)
        for item in items:
            if triage.get(item['project_id'], {}).get('policy_error'):
                item['delegable'] = False
                continue
            try:
                self.attention.require_delegable(item['id'])
            except (ValueError, LookupError):
                item['delegable'] = False
            else:
                item['delegable'] = True
        return {**document, "focus": asdict(self.workspace.focus_snapshot()),
                "attention": items, "triage": triage}

    def with_work(self, document: dict[str, Any]) -> dict[str, Any]:
        """Give each job and session the work item its run is linked to, or null when none is."""
        # Every store write records a history entry, so the latest sequence is the store's revision.
        # Read it before the links so a write in between is picked up by the next request.
        revision = (self.store, self.store.latest_sequence())
        cached = self.work_links
        if cached is None or cached[0] != revision:
            execution = self.container.execution()
            cached = self.work_links = (revision, self.container.run_work(execution=execution),
                                       {(run.host, run.remote_job_id): run.id for run in execution.runs()})
        links = cached[1]
        deliveries = self.container.execution().deliveries()

        def decisions(item, run_id):
            entries = {decision['id']: decision for decision in item.get('decisions_since_dispatch', [])}
            for delivery in deliveries:
                if delivery.run == run_id and delivery.key.startswith('context-decision:'):
                    decision = json.loads(delivery.answer)
                    entries[decision['id']] = dict(decision, delivery_status=delivery.status,
                                                    delivery_error=delivery.error)
            return {'decisions_since_dispatch': list(entries.values())} if entries else {}

        return {**document, "hosts": [{**host, **{kind: [{**item, "work": links.get((host["name"], item["id"])),
                                                        "audit_run_id": cached[2].get((host["name"], item["id"])),
                                                        **decisions(item, cached[2].get((host["name"], item["id"])))}
                                                         for item in host[kind]] for kind in ("jobs", "sessions")}}
                                      for host in document["hosts"]]}

    def report_pipeline(self, host: str, name: str, run: dict[str, Any] | None,
                        baseline: dict[str, Any] | None) -> None:
        """A host's latest summary of a pipeline's run; browsers get it as a pipeline event, not a new document."""
        with self.changed:
            self.pipeline_seq += 1
            self.pipeline_runs[(host, name)] = {"run": run, "baseline": baseline, "seq": self.pipeline_seq}
            self.changed.notify_all()

    def pipelines(self, registry: Registry, hosts: dict[str, dict[str, Any]],
                  after: int | None = None) -> list[dict[str, Any]]:
        """Declared pipelines, reported or not, and any other a host reports; with `after`, only reports since that seq.

        A declared pipeline names the room (project label) it belongs to; one nobody declared has no room.
        `host_ok` and `host_error` say whether its host is reachable now; `run` is the last report, or null."""
        with self.changed:
            reported = dict(self.pipeline_runs)
        keys = [(entry.get("host"), name) for name, entry in self.pipeline_config.items()]
        keys += [key for key in sorted(reported) if key[1] not in self.pipeline_config]
        out = []
        for host, name in keys:
            report = reported.get((host, name)) or {"run": None, "baseline": None, "seq": 0}
            if after is not None and report["seq"] <= after:
                continue
            declared = self.pipeline_config.get(name)
            label = declared.get("project") if declared else None
            project = registry.project_for(host, label) if host and label else None
            host_entry = hosts.get(host) or {"ok": False, "error": f"{host} is not a host this deck follows"}
            out.append({"host": host, "pipeline": name, "project": label, "project_id": project.id if project else None,
                        "declared": declared is not None, "host_ok": bool(host_entry.get("ok")),
                        "host_error": host_entry.get("error"), "run": report["run"], "baseline": report["baseline"],
                        "seq": report["seq"]})
        return out

    def wait_for_change(self, seen_version: int, timeout: float, seen_pipelines: int | None = None) -> int:
        """Also wakes when a snooze ends, so the item comes back on every deck without a reload, and when a pipeline
        report arrives if `seen_pipelines` is given (compare `pipeline_seq` to tell)."""
        now = self.attention.clock().timestamp()
        ends = [item.snooze_until.timestamp() for item in self.attention.list()
                if item.snooze_until is not None and item.snooze_until.timestamp() > self.woken_until]
        ending = min(ends) if ends else None
        wait = timeout if ending is None else max(0.0, min(timeout, ending - now))
        with self.changed:
            self.changed.wait_for(lambda: self.version != seen_version or (
                seen_pipelines is not None and self.pipeline_seq != seen_pipelines), timeout=wait)
            if self.version == seen_version and ending is not None and self.attention.clock().timestamp() >= ending:
                self.woken_until = max(self.woken_until, ending)
                self.version += 1
            return self.version
