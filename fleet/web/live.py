"""What the deck writes and derives on top of host state, shared by live and fixture decks.

A state class using LiveWorkspace provides `changed` (a Condition), `version`,
`workspace` (a WorkspaceStore), `attention` (an AttentionFacade), `registry` (the project
Registry in use), `project_labels`, `capacity`, `known_projects()`, `host_names()`,
`edit_registry()` and `repository_remotes()`.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Container, TypeVar

from fleet.modules.attention import AttentionFacade
from fleet.projections.attention import attention_items
from fleet.building import NoVacancy
from fleet.projects import Registry
from fleet.transport import FleetError
from fleet.workspace import NotShuttered, WorkspaceStore

MOVE_IN_LOCK = threading.Lock()   # moving in, linking, merging, shuttering and restoring: one at a time
T = TypeVar("T")


class AlreadyHoused(FleetError):
    """The host's label already belongs to a registered project."""


class LiveWorkspace:
    changed: threading.Condition
    version: int
    workspace: WorkspaceStore
    attention: AttentionFacade
    woken_until: float
    registry: Registry
    project_labels: dict[str, str]
    capacity: int
    pipeline_config: dict[str, dict[str, str]]           # name → {"host", "project": room label}, as configured
    pipeline_runs: dict[tuple[str, str], dict[str, Any]]  # (host, name) → {"run", "baseline", "seq"} as last reported
    pipeline_seq: int

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

    def housed(self, hosts: list[str], label: str) -> None:
        """Refuse when any host's label already belongs to a project. Call with the lock."""
        for host in hosts:
            if self.registry.project_for(host, label):
                raise AlreadyHoused(f"{host}:{label} already belongs to {self.registry.project_for(host, label).id}")

    def move_in(self, hosts: list[str], label: str, shutter: str | None = None) -> dict[str, Any]:
        """Register an unregistered label on `hosts` as one project on the lowest free floor, named as its room is.
        When the building is full the only way in is to shutter a floor first (`shutter`); capacity never grows here."""
        self.check_hosts(hosts, label)
        with MOVE_IN_LOCK:
            self.known_projects()   # the registry as it is on disk now
            self.housed(hosts, label)
            self.make_room(shutter)

            def register(registry: Registry) -> str:
                project = registry.create(self.project_labels.get(label) or label)
                for host in hosts:
                    registry.link(project.id, host, label)
                return project.id

            project_id = self.edit_registry(register)
            floor = self.workspace.move_in(project_id, self.capacity)
        self.bump()
        return {"project_id": project_id, "floor": floor}

    def link_in(self, project_id: str, hosts: list[str], label: str) -> dict[str, Any]:
        """Link the label on `hosts` to an existing project: its work joins that project, and no floor is taken."""
        self.check_hosts(hosts, label)
        with MOVE_IN_LOCK:
            if project_id not in self.known_projects():
                raise LookupError(f"no project '{project_id}'")
            self.housed(hosts, label)

            def link(registry: Registry) -> None:
                for host in hosts:
                    registry.link(project_id, host, label)

            self.edit_registry(link)
        self.bump()
        return {"project_id": project_id, "floor": self.workspace.floors_snapshot().get(project_id)}

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
        with MOVE_IN_LOCK:
            for project_id in (keep, other):
                if project_id not in self.known_projects():
                    raise LookupError(f"no project '{project_id}'")
            self.edit_registry(lambda registry: registry.merge(keep, other))
            freed = self.workspace.forget_project(other)
        self.bump()
        return {"project_id": keep, "merged": other, "freed": freed,
                "floor": self.workspace.floors_snapshot().get(keep)}

    def shutter(self, project_id: str) -> dict[str, Any]:
        """Pack a project away in the storehouse (ADR 0005): its floor is freed; its ID, links and records stay."""
        with MOVE_IN_LOCK:
            if project_id not in self.known_projects():
                raise LookupError(f"no project '{project_id}'")
            floor = self.workspace.shutter(project_id, time.time())
        self.bump()
        return {"project_id": project_id, "floor": floor}

    def restore(self, project_id: str, shutter: str | None = None) -> dict[str, Any]:
        """Move a crate back in: to its old floor if free, else the lowest free one; when full, only by shuttering."""
        with MOVE_IN_LOCK:
            if project_id not in self.known_projects():
                raise LookupError(f"no project '{project_id}'")
            if project_id not in self.workspace.shuttered_snapshot():
                raise NotShuttered(f"{project_id} is not in the storehouse")
            self.make_room(shutter)
            floor = self.workspace.restore(project_id, self.capacity)
        self.bump()
        return {"project_id": project_id, "floor": floor}

    def make_room(self, shutter: str | None) -> None:
        """Shutter `shutter` if given (it must hold a floor); then there must be a free floor. Call with the lock."""
        self.workspace.settle(self.registry.projects, self.capacity)
        floors = self.workspace.floors_snapshot()
        if shutter is not None:
            if floors.get(shutter, self.capacity + 1) > self.capacity:
                raise FleetError(f"{shutter} holds no floor to clear")
            self.workspace.shutter(shutter, time.time())
        elif len({floor for floor in floors.values() if floor <= self.capacity}) >= self.capacity:
            raise NoVacancy("The building's full: shutter a floor to make room")

    def with_building(self, document: dict[str, Any], registry: Registry) -> dict[str, Any]:
        """Add the floors registered projects occupy within capacity with each one's focus, the projects in the
        storehouse, and the live projects that have no floor."""
        self.workspace.settle(registry.projects, self.capacity)
        floors = {project_id: floor for project_id, floor in self.workspace.floors_snapshot().items()
                  if project_id in registry.projects and floor <= self.capacity}
        shuttered = self.workspace.shuttered_snapshot()
        focus = {project_id: self.workspace.focus_of({"project_id": project_id}) for project_id in floors}
        return {**document, "building": {"capacity": self.capacity, "floors": floors, "focus": focus,
                                         "shuttered": shuttered,
                                         "no_floor": [project_id for project_id in registry.projects
                                                      if project_id not in floors and project_id not in shuttered]}}

    def bump(self) -> None:
        """Push a new document to every browser."""
        with self.changed:
            self.version += 1
            self.changed.notify_all()

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        self.workspace.set_focus(focus, projects, labels, self.known_projects())
        self.bump()

    def document(self) -> dict[str, Any]:
        raise NotImplementedError

    def act_on_attention(self, action: str, item_id: str, seconds: float | None = None) -> None:
        if action == "acknowledge":
            self.attention.acknowledge(item_id, actor="web-user")
        elif action == "snooze":
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
                raise FleetError("snooze needs a positive number of seconds")
            self.attention.snooze(item_id, until=self.attention.clock() + timedelta(seconds=seconds), actor="web-user")
        elif action == "reopen":
            self.attention.reopen(item_id, actor="web-user")
        else:
            raise FleetError(f"unknown attention action '{action}'")
        self.bump()

    def with_attention(self, document: dict[str, Any]) -> dict[str, Any]:
        """Add stored focus choices and the Attention projection."""
        return {**document, "focus": self.workspace.focus_snapshot(),
                "attention": attention_items(self.attention, document["hosts"])}

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
