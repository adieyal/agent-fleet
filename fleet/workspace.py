"""Live workspace state, kept on the machine running `fleet web` in `workspace.json` beside
the Fleet config — not in Git and not in the project registry. It holds the user's own
choices; everything else is derived from the host streams.

Focus (CONTEXT.md: Focus) is `priority` or `background`. A job or session is focused
through its registered project when its (host, label) is linked, otherwise through its
label, so unregistered groups can be focused too. Only choices the user made are stored;
anything without one is in priority, so the deck looks as it did before focus existed.

Attention actions are what the user did to an attention item (see fleet.attention):
acknowledged, or snoozed until a time. Items themselves are derived and never stored.

Floors are which floor of the building each registered project occupies (see
fleet.building), numbered from 1 above the lobby. A shuttered project has no floor; it
is recorded with when it was shuttered and the floor it left, so restoring it can take
that floor back if it is free. Its ID, links, focus and everything else are untouched.

    {"focus": {"projects": {"p-1a2b3c4d": "background"}, "labels": {"scratch": "background"}},
     "attention": {"<item id>": {"state": "snoozed", "at": 1790400000.0, "until": 1790403600.0}},
     "floors": {"p-1a2b3c4d": 1},
     "shuttered": {"p-5e6f7a8b": {"at": 1790400000.0, "floor": 2}}}
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Container, Iterable

from fleet.building import NoVacancy
from fleet.transport import FleetError

FOCUSES = ("priority", "background")
DEFAULT_FOCUS = "priority"
ACTIONS = ("acknowledged", "snoozed")


class AlreadyShuttered(FleetError):
    """The project is already in the storehouse."""


class NotShuttered(FleetError):
    """Only a project in the storehouse can be restored."""


class WorkspaceStore:
    """The user's choices, written through to `path`; with no path they live in memory only (fixtures)."""

    def __init__(self, path: Path | None, initial: dict[str, Any] | None = None) -> None:
        self.path = path
        self.lock = threading.Lock()
        if path is not None and path.exists():
            try:
                initial = json.loads(path.read_text())
            except ValueError as error:
                raise FleetError(f"{path} is not valid JSON: {error}") from None
        initial = initial or {}
        self.focus: dict[str, dict[str, str]] = {"projects": {}, "labels": {}}
        for kind in self.focus:
            for key, focus in ((initial.get("focus") or {}).get(kind) or {}).items():
                if focus not in FOCUSES:
                    raise FleetError(f"focus for {kind[:-1]} '{key}' is priority or background, not '{focus}'")
                self.focus[kind][key] = focus
        self.attention: dict[str, dict[str, Any]] = {}
        for item_id, action in (initial.get("attention") or {}).items():
            if action.get("state") not in ACTIONS or (action["state"] == "snoozed" and not action.get("until")):
                raise FleetError(f"attention item '{item_id}' has an unknown action {action}")
            self.attention[item_id] = dict(action)
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

    def save(self) -> None:
        """Write the whole file atomically; call with the lock held."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"focus": self.focus, "attention": self.attention, "floors": self.floors,
                                         "shuttered": self.shuttered}, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, self.path)

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
            self.save()

    # ------------------------------------------------------------ attention actions
    def actions(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {item_id: dict(action) for item_id, action in self.attention.items()}

    def act(self, item_id: str, action: dict[str, Any] | None) -> None:
        """Record what the user did to an item; None clears it (reopen)."""
        with self.lock:
            if action is None:
                self.attention.pop(item_id, None)
            else:
                self.attention[item_id] = action
            self.save()

    def forget(self, item_ids: Iterable[str]) -> None:
        """Drop actions for items that are gone for good."""
        with self.lock:
            gone = [item_id for item_id in item_ids if item_id in self.attention]
            for item_id in gone:
                del self.attention[item_id]
            if gone:
                self.save()

    # ------------------------------------------------------------ floors
    def floors_snapshot(self) -> dict[str, int]:
        with self.lock:
            return dict(self.floors)

    def move_in(self, project_id: str, capacity: int) -> int:
        """The project's floor: the one it has, else the lowest free one within capacity."""
        with self.lock:
            if project_id not in self.floors:
                self.floors[project_id] = self.free_floor(capacity)
                self.save()
            return self.floors[project_id]

    def settle(self, project_ids: Iterable[str], capacity: int) -> None:
        """Free the floors of projects no longer registered and move in registered ones that have none and aren't
        shuttered, in order, while floors are free. A floor once held is never reassigned, even above a lowered
        capacity."""
        project_ids = list(project_ids)
        with self.lock:
            changed = False
            for records in (self.floors, self.shuttered):
                for project_id in [known for known in records if known not in project_ids]:
                    del records[project_id]
                    changed = True
            for project_id in project_ids:
                if project_id in self.floors or project_id in self.shuttered:
                    continue
                try:
                    self.floors[project_id] = self.free_floor(capacity)
                except NoVacancy:
                    break
                changed = True
            if changed:
                self.save()

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
            self.save()
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
            self.save()
            return floor

    def free_floor(self, capacity: int) -> int:
        """The lowest free floor; call with the lock held."""
        taken = set(self.floors.values())
        free = next((floor for floor in range(1, capacity + 1) if floor not in taken), None)
        if free is None:
            raise NoVacancy("The building's full: every floor is taken")
        return free
