"""Focus, stable floors and shutter transitions for a workspace."""
from __future__ import annotations

import threading
from typing import Any, Container, Iterable

from .building import NoVacancy
from fleet.transport import FleetError

FOCUSES = ("priority", "background")
DEFAULT_FOCUS = "priority"


class AlreadyHoused(FleetError):
    """A host label already belongs to a project."""


class AlreadyShuttered(FleetError):
    """The project is already in the storehouse."""


class NotShuttered(FleetError):
    """Only a project in the storehouse can be restored."""


class Choices:
    """Workspace choices within one transaction."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.lock = threading.Lock()
        initial = initial or {}
        self.focus: dict[str, dict[str, str]] = {"projects": {}, "labels": {}}
        for kind in self.focus:
            for key, focus in ((initial.get("focus") or {}).get(kind) or {}).items():
                if focus not in FOCUSES:
                    raise FleetError(f"focus for {kind[:-1]} '{key}' is priority or background, not '{focus}'")
                self.focus[kind][key] = focus
        self.floors: dict[str, int] = {}
        for project_id, floor in (initial.get("floors") or {}).items():
            if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1 or floor in self.floors.values():
                raise FleetError(f"project '{project_id}' has floor {floor!r}: floors are distinct whole numbers from 1")
            self.floors[project_id] = floor
        self.shuttered: dict[str, dict[str, Any]] = {}
        for project_id, record in (initial.get("shuttered") or {}).items():
            floor = record.get("floor")
            if project_id in self.floors or not isinstance(record.get("at"), (int, float)) or not (
                    floor is None or (isinstance(floor, int) and not isinstance(floor, bool) and floor >= 1)):
                raise FleetError(f"shuttered project '{project_id}' has an unknown record {record}")
            self.shuttered[project_id] = {"at": record["at"], "floor": floor}

    # ------------------------------------------------------------ focus
    def focus_snapshot(self) -> dict[str, dict[str, str]]:
        with self.lock:
            return {kind: dict(choices) for kind, choices in self.focus.items()}

    def focus_of(self, item: dict[str, Any]) -> str:
        """A job's or session's focus: its project's when linked, else its label's."""
        with self.lock:
            if item.get("project_id"):
                return self.focus["projects"].get(item["project_id"], DEFAULT_FOCUS)
            return self.focus["labels"].get(item.get("project") or "", DEFAULT_FOCUS)

    def annotate(self, item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "focus": self.focus_of(item)}

    def set_focus(self, focus: str, projects: Iterable[str], labels: Iterable[str], known_projects: Container[str]) -> None:
        """Store a choice for registered projects (by ID) and unlinked labels; refuse anything else."""
        projects, labels = list(projects), list(labels)
        if focus not in FOCUSES:
            raise FleetError(f"focus is priority or background, not '{focus}'")
        unknown = [project_id for project_id in projects if project_id not in known_projects]
        if unknown:
            raise FleetError(f"unknown project {', '.join(unknown)}")
        if not all(labels):
            raise FleetError("labels must not be empty")
        with self.lock:
            for project_id in projects:
                self.focus["projects"][project_id] = focus
            for label in labels:
                self.focus["labels"][label] = focus

    # ------------------------------------------------------------ floors
    def floors_snapshot(self) -> dict[str, int]:
        with self.lock:
            return dict(self.floors)

    def move_in(self, project_id: str, capacity: int) -> int:
        """The project's floor: the one it has, else the lowest free one within capacity."""
        with self.lock:
            if project_id not in self.floors:
                self.floors[project_id] = self.free_floor(capacity)
            return self.floors[project_id]

    def settle(self, project_ids: Iterable[str], capacity: int) -> None:
        """Free the floors of projects no longer registered and move in registered ones that have none and aren't
        shuttered, in order, while floors are free. A floor once held is never reassigned, even above a lowered
        capacity."""
        project_ids = list(project_ids)
        with self.lock:
            for records in (self.floors, self.shuttered):
                for project_id in [known for known in records if known not in project_ids]:
                    del records[project_id]
            for project_id in project_ids:
                if project_id in self.floors or project_id in self.shuttered:
                    continue
                try:
                    self.floors[project_id] = self.free_floor(capacity)
                except NoVacancy:
                    break

    def shuttered_snapshot(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {project_id: dict(record) for project_id, record in self.shuttered.items()}

    def shutter(self, project_id: str, now: float) -> int | None:
        """Take the project off its floor into the storehouse; return the floor it freed."""
        with self.lock:
            if project_id in self.shuttered:
                raise AlreadyShuttered(f"{project_id} is already in the storehouse")
            floor = self.floors.pop(project_id, None)
            self.shuttered[project_id] = {"at": now, "floor": floor}
            return floor

    def restore(self, project_id: str, capacity: int) -> int:
        """Bring a shuttered project back: to the floor it left if that is free, else the lowest free one."""
        with self.lock:
            record = self.shuttered.get(project_id)
            if record is None:
                raise NotShuttered(f"{project_id} is not in the storehouse")
            old = record["floor"]
            if old is not None and old <= capacity and old not in self.floors.values():
                floor = old
            else:
                floor = self.free_floor(capacity)
            del self.shuttered[project_id]
            self.floors[project_id] = floor
            return floor

    def forget_project(self, project_id: str) -> int | None:
        """Drop a project merged into another: its focus, its floor or crate. Return the floor it freed."""
        with self.lock:
            self.focus["projects"].pop(project_id, None)
            self.shuttered.pop(project_id, None)
            floor = self.floors.pop(project_id, None)
            return floor

    def free_floor(self, capacity: int) -> int:
        """The lowest free floor; call with the lock held."""
        taken = set(self.floors.values())
        free = next((floor for floor in range(1, capacity + 1) if floor not in taken), None)
        if free is None:
            raise NoVacancy("The building's full: every floor is taken")
        return free
