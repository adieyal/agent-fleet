"""Serves a recorded fleet from a JSON file instead of following hosts, for browser tests and demos.

A fixture holds the hosts of an `/api/state` document, the project registry as it
is stored in the Fleet config, focus choices as in `focus.json`, and the Markdown
behind them:

    {"time": …, "project_labels": {…}, "hosts": [{name, ok, error, jobs, sessions}, …],
     "projects": {"p-…": {"name": …, "links": […]}, …},
     "focus": {"projects": {"p-…": "background"}, "labels": {"<label>": "background"}},
     "job_documents": {"<host>/<job>/<document id>": "markdown", …},
     "library": {"<project>": [{"id": "README.md", "mtime": …, "markdown": "…"}, …]}}

Jobs and sessions gain `project_id` and `focus`, as they do live. Timestamps
are served as recorded; a browser test pins its clock to `time`. Focus can be set,
in memory only, so the recorded file never changes.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fleet.focus import FocusStore
from fleet.projects import Registry
from fleet.transport import FleetError
from fleet.web.documents import STATUS_LINE, render_markdown


class FixtureState:
    """Same surface the HTTP handler uses on FleetState; only focus ever changes."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.registry = Registry.from_config({"projects": fixture.get("projects", {})})
        self.focus = FocusStore(None, fixture.get("focus"))
        self.changed = threading.Condition()
        self.version = 0

    @classmethod
    def load(cls, path: str | Path) -> FixtureState:
        return cls(json.loads(Path(path).read_text()))

    def host_names(self) -> list[str]:
        return [host["name"] for host in self.fixture["hosts"]]

    def document(self) -> dict[str, Any]:
        with self.changed:
            return {"time": self.fixture["time"], "project_labels": self.fixture.get("project_labels", {}),
                    "projects": [{"id": project_id, **entry} for project_id, entry in self.registry.to_config().items()],
                    "projects_error": None, "focus": self.focus.snapshot(), "hosts": [
                {**host, "jobs": [self.focus.annotate(self.registry.resolve(host["name"], job)) for job in host["jobs"]],
                 "sessions": [self.focus.annotate(self.registry.resolve(host["name"], session))
                              for session in host["sessions"]]}
                for host in self.fixture["hosts"]]}

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        with self.changed:
            self.focus.set(focus, projects, labels, self.registry.projects)
            self.version += 1
            self.changed.notify_all()

    def wait_for_change(self, seen_version: int, timeout: float) -> int:
        with self.changed:
            self.changed.wait_for(lambda: self.version != seen_version, timeout=timeout)
            return self.version

    def read_document(self, host_name: str, job_id: str, document_id: str) -> dict[str, Any]:
        """What /api/doc returns for a live host: the job's document entry plus rendered Markdown."""
        host = next(host for host in self.fixture["hosts"] if host["name"] == host_name)
        job = next((job for job in host["jobs"] if job["id"] == job_id), None)
        entry = next((doc for doc in (job or {}).get("documents", []) if doc["id"] == document_id), None)
        markdown = self.fixture.get("job_documents", {}).get(f"{host_name}/{job_id}/{document_id}")
        if job is None or entry is None or markdown is None:
            raise FleetError(f"job {job_id} has no document {document_id}")
        return {**entry, "truncated": False, "job": job_id, "project": job["project"], "agent": job["agent"],
                "job_description": job["description"], "host": host_name,
                **render_markdown(STATUS_LINE.sub("", markdown).strip())}


class FixtureLibrary:
    """Same surface as ProjectLibrary, over the fixture's `library` section."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.projects: dict[str, list[dict[str, Any]]] = fixture.get("library", {})

    def list(self) -> list[dict[str, Any]]:
        return [{"project": project, "id": doc["id"], "name": Path(doc["id"]).name, "title": title(doc),
                 "kind": "file", "size": len(doc["markdown"].encode()), "mtime": doc["mtime"]}
                for project, docs in sorted(self.projects.items()) for doc in docs]

    def read(self, project: str, document_id: str) -> dict[str, Any] | None:
        doc = next((doc for doc in self.projects.get(project, []) if doc["id"] == document_id), None)
        if doc is None:
            return None
        return {"project": project, "id": document_id, "name": Path(document_id).name, "kind": "file",
                "size": len(doc["markdown"].encode()), "mtime": doc["mtime"], "truncated": False,
                **render_markdown(doc["markdown"])}


def title(doc: dict[str, Any]) -> str:
    heading = next((line[2:].strip() for line in doc["markdown"].splitlines()[:40] if line.startswith("# ")), None)
    return heading or Path(doc["id"]).stem
