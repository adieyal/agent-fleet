"""What the deck writes and derives on top of host state, shared by live and fixture decks.

A state class using LiveWorkspace provides `changed` (a Condition), `version`,
`workspace` (a WorkspaceStore), `board` (an AttentionBoard), `registry` (the project
Registry in use), `project_labels`, `capacity`, `known_projects()`, `host_names()` and
`register()`.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Container

from fleet.attention import AttentionBoard
from fleet.building import NoVacancy
from fleet.projects import Registry
from fleet.transport import FleetError
from fleet.workspace import WorkspaceStore

MOVE_IN_LOCK = threading.Lock()


class AlreadyHoused(FleetError):
    """The host's label already belongs to a registered project."""


class LiveWorkspace:
    changed: threading.Condition
    version: int
    workspace: WorkspaceStore
    board: AttentionBoard
    registry: Registry
    project_labels: dict[str, str]
    capacity: int

    def known_projects(self) -> Container[str]:
        raise NotImplementedError

    def host_names(self) -> list[str]:
        raise NotImplementedError

    def register(self, name: str, host: str, label: str) -> str:
        """Register a project linked to host:label and return its ID."""
        raise NotImplementedError

    def move_in(self, host: str, label: str) -> dict[str, Any]:
        """Register an unregistered label as a project on the lowest free floor, named as its room is."""
        if host not in self.host_names() or not label:
            raise FleetError("a known host and a label are required")
        with MOVE_IN_LOCK:
            self.known_projects()   # the registry as it is on disk now
            if self.registry.project_for(host, label):
                raise AlreadyHoused(f"{host}:{label} already belongs to {self.registry.project_for(host, label).id}")
            self.workspace.settle(self.registry.projects, self.capacity)
            if len(set(self.workspace.floors_snapshot().values()) & set(range(1, self.capacity + 1))) >= self.capacity:
                raise NoVacancy("The building's full: every floor is taken")
            project_id = self.register(self.project_labels.get(label) or label, host, label)
            floor = self.workspace.move_in(project_id, self.capacity)
        self.bump()
        return {"project_id": project_id, "floor": floor}

    def with_building(self, document: dict[str, Any], registry: Registry) -> dict[str, Any]:
        """Add the floors registered projects occupy within capacity, and the projects that have none."""
        self.workspace.settle(registry.projects, self.capacity)
        floors = {project_id: floor for project_id, floor in self.workspace.floors_snapshot().items()
                  if project_id in registry.projects and floor <= self.capacity}
        return {**document, "building": {"capacity": self.capacity, "floors": floors,
                                         "no_floor": [project_id for project_id in registry.projects
                                                      if project_id not in floors]}}

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
        self.document()   # brings the board up to date: an item may have resolved since the last push
        self.board.act(item_id, action, time.time(), seconds)
        self.bump()

    def with_attention(self, document: dict[str, Any]) -> dict[str, Any]:
        """Add the stored focus choices and attention items derived from the document's hosts."""
        return {**document, "focus": self.workspace.focus_snapshot(),
                "attention": self.board.items(document["hosts"], time.time())}

    def wait_for_change(self, seen_version: int, timeout: float) -> int:
        """Also wakes when a snooze ends, so the item comes back on every deck without a reload."""
        now = time.time()
        ending = self.board.snooze_ending(now)
        wait = timeout if ending is None else max(0.0, min(timeout, ending - now))
        with self.changed:
            self.changed.wait_for(lambda: self.version != seen_version, timeout=wait)
            if self.version == seen_version and ending is not None and time.time() >= ending:
                self.board.announce(ending)
                self.version += 1
            return self.version
