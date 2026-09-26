"""Focus: the user's choice of where resources go, `priority` or `background` (CONTEXT.md: Focus).

Focus is live workspace state, kept on the machine running `fleet web` in `focus.json`
beside the Fleet config — not in Git and not in the project registry. A job or session
is focused through its registered project when its (host, label) is linked, otherwise
through its label, so unregistered groups can be focused too:

    {"projects": {"p-1a2b3c4d": "background"}, "labels": {"scratch": "background"}}

Only choices the user made are stored. Anything without one is in priority, so the
deck looks as it did before focus existed until the user flips a switch.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Container, Iterable

from fleet.transport import FleetError

FOCUSES = ("priority", "background")
DEFAULT = "priority"


class FocusStore:
    """Focus choices, written through to `path`; with no path they live in memory only (fixtures)."""

    def __init__(self, path: Path | None, initial: dict[str, Any] | None = None) -> None:
        self.path = path
        self.lock = threading.Lock()
        if path is not None and path.exists():
            try:
                initial = json.loads(path.read_text())
            except ValueError as error:
                raise FleetError(f"{path} is not valid JSON: {error}") from None
        self.choices = {"projects": {}, "labels": {}}
        for kind in self.choices:
            for key, focus in ((initial or {}).get(kind) or {}).items():
                if focus not in FOCUSES:
                    raise FleetError(f"focus for {kind[:-1]} '{key}' is priority or background, not '{focus}'")
                self.choices[kind][key] = focus

    def snapshot(self) -> dict[str, dict[str, str]]:
        with self.lock:
            return {kind: dict(choices) for kind, choices in self.choices.items()}

    def focus_of(self, item: dict[str, Any]) -> str:
        """A job's or session's focus: its project's when linked, else its label's."""
        with self.lock:
            if item.get("project_id"):
                return self.choices["projects"].get(item["project_id"], DEFAULT)
            return self.choices["labels"].get(item.get("project") or "", DEFAULT)

    def annotate(self, item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "focus": self.focus_of(item)}

    def set(self, focus: str, projects: Iterable[str], labels: Iterable[str], known_projects: Container[str]) -> None:
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
                self.choices["projects"][project_id] = focus
            for label in labels:
                self.choices["labels"][label] = focus
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.choices, indent=2, sort_keys=True) + "\n")
                os.replace(temporary, self.path)
