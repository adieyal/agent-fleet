"""Serves a recorded fleet from a JSON file instead of following hosts, for browser tests and demos.

A fixture holds the hosts of an `/api/state` document, the project registry as it
is stored in the Fleet config, focus choices as in `workspace.json`, and the Markdown
behind them:

    {"time": …, "project_labels": {…}, "hosts": [{name, ok, error, jobs, sessions}, …],
     "projects": {"p-…": {"name": …, "links": […]}, …},
     "focus": {"projects": {"p-…": "background"}, "labels": {"<label>": "background"}},
     "capacity": 10, "floors": {"p-…": 1},   # both optional: capacity 6, floors assigned as projects move in
     "shuttered": {"p-…": {"at": …, "floor": 2}},   # optional: projects in the storehouse
     "remotes": {"<host>": {"<cwd>": ["git@…"]}},   # optional: repository remotes, for move-in offers
     "job_documents": {"<host>/<job>/<document id>": "markdown", …},
     "pipelines": {"<pipeline>": {"host": …, "project": "<label>"}},   # optional: as in the Fleet config
     "pipeline_reports": [{"host": …, "pipeline": …, "run": …, "baseline": …}, …],   # as fleetd streams them
     "library": {"<project>": [{"id": "README.md", "mtime": …, "markdown": "…"}, …]}}

Jobs and sessions gain `project_id` and `focus`, and attention items are ingested into an isolated store. Timestamps
are served as recorded; a browser test pins its clock to `time`. Focus and attention
actions can be set in the temporary store, so the recorded file never changes.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from fleet.composition import open_attention, open_store, open_workspace, open_work
from fleet.modules.workspace import Registry
from fleet.projections.workspace import annotate, resolve, registry_config
from fleet.transport import FleetError
from fleet.web.documents import STATUS_LINE, render_markdown
from fleet.web.job_store import ProjectDocuments
from fleet.web.library import ProjectLibrary, is_private
from fleet.web.live import LiveWorkspace


class FixtureState(LiveWorkspace):
    """Same surface the HTTP handler uses on FleetState; only the user's choices ever change."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.project_labels = fixture.get("project_labels", {})
        self.attention_directory = TemporaryDirectory(prefix="fleet-fixture-")
        store = open_store(Path(self.attention_directory.name) / "fleet.db")
        self.store = store
        self.workspace = open_workspace(store, initial=fixture, actor="fixture-user")
        work = open_work(store)
        identities = {}
        for item in fixture.get("work_items", []):
            parent = identities[item["parent"]] if item["parent"] is not None else None
            identities[item["key"]] = work.add(project=item["project"], title=item["title"],
                goal=item["goal"], kind=item["kind"], parent=parent, actor="fixture-user").id
        self.registry = self.workspace.registry()
        self.capacity = self.workspace.capacity()
        self.attention = open_attention(store,
                                        workspace_path=Path(self.attention_directory.name) / "workspace.json")
        self.woken_until = 0.0
        for host in fixture["hosts"]:
            self.attention.observe({**host,
                "jobs": [resolve(self.registry, host["name"], job) for job in host["jobs"]],
                "sessions": [resolve(self.registry, host["name"], session) for session in host["sessions"]]})
        self.changed = threading.Condition()
        self.version = 0
        self.pipeline_config = fixture.get("pipelines", {})
        self.pipeline_runs = {(report["host"], report["pipeline"]): {"run": report.get("run"),
                                                                     "baseline": report.get("baseline"), "seq": 1}
                              for report in fixture.get("pipeline_reports", [])}
        self.pipeline_seq = 1 if self.pipeline_runs else 0
        self.documents = ProjectDocuments(Path(self.attention_directory.name) / "projects")
        self.keep_recorded_documents()

    def keep_recorded_documents(self) -> None:
        """Fill the document store as fleet web would have while it followed the recorded hosts."""
        for host in self.fixture["hosts"]:
            for job in host["jobs"]:
                project_id = resolve(self.registry, host["name"], job)["project_id"]
                if project_id is None:
                    continue
                for document in self.documents.observe(project_id, host["name"], job):
                    markdown = self.fixture.get("job_documents", {}).get(f"{host['name']}/{job['id']}/{document['id']}")
                    if markdown is None:
                        self.documents.failed(project_id, host["name"], job["id"], document["id"],
                                              "the fixture records no Markdown for this document")
                    else:
                        self.documents.keep(project_id, host["name"], job["id"], document, markdown)

    def job_hosts(self) -> dict[str, tuple[bool, set[str]]]:
        return {host["name"]: (bool(host.get("ok")), {job["id"] for job in host["jobs"]}) for host in self.fixture["hosts"]}

    @classmethod
    def load(cls, path: str | Path) -> FixtureState:
        fixture = json.loads(Path(path).read_text())
        # each a folder relative to the fixture file, or {"path": folder, "recursive": true} as in the Fleet config
        fixture["library_roots"] = {project: str(Path(path).parent / entry) if isinstance(entry, str)
                                    else {**entry, "path": str(Path(path).parent / entry["path"])}
                                    for project, entry in fixture.get("library_roots", {}).items()}
        return cls(fixture)

    def host_names(self) -> list[str]:
        return [host["name"] for host in self.fixture["hosts"]]

    def edit_registry(self, change: Callable[[Registry], Any]) -> Any:
        result = self.workspace.edit_registry(change)
        self.registry = self.workspace.registry()
        return result

    def repository_remotes(self, host: str, directories: list[str]) -> dict[str, list[str]]:
        recorded = self.fixture.get("remotes", {}).get(host, {})
        return {directory: list(recorded.get(directory, [])) for directory in directories}

    def document(self) -> dict[str, Any]:
        self.registry = self.workspace.registry()
        with self.changed:
            document = self.with_attention({"time": self.fixture["time"], "project_labels": self.project_labels,
                    "projects": [{"id": project_id, **entry} for project_id, entry in registry_config(self.registry).items()],
                    "projects_error": None, "hosts": [
                {**host, "jobs": [annotate(self.workspace, resolve(self.registry, host["name"], job)) for job in host["jobs"]],
                 "sessions": [annotate(self.workspace, resolve(self.registry, host["name"], session))
                              for session in host["sessions"]]}
                for host in self.fixture["hosts"]]})
        document = self.with_building(document, self.registry)
        document["building"]["capacity_error"] = None
        document["pipelines"] = self.pipelines(self.registry, {host["name"]: host for host in self.fixture["hosts"]})
        return document

    def pipeline_updates(self, after: int) -> list[dict[str, Any]]:
        return self.pipelines(self.registry, {host["name"]: host for host in self.fixture["hosts"]}, after)

    def known_projects(self) -> dict[str, Any]:
        self.registry = self.workspace.registry()
        return self.registry.projects

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

    def read_asset(self, host_name: str, job_id: str, document_id: str, asset_path: str) -> tuple[str, bytes]:
        raise FleetError(f"job {job_id} has no asset {asset_path}")  # recorded fleets carry no images


class FixtureLibrary:
    """Same surface as ProjectLibrary, over the fixture's `library` section and any real `library_roots`
    (folders, relative to the fixture file), which are read as a ProjectLibrary reads them."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.projects: dict[str, list[dict[str, Any]]] = fixture.get("library", {})
        self.files = ProjectLibrary(fixture.get("library_roots", {}))
        self.roots = {**{project: None for project in self.projects}, **self.files.roots}

    def root(self, project: str) -> Path | None:
        return self.files.root(project)

    def list(self) -> list[dict[str, Any]]:
        return sorted([{"project": project, "id": doc["id"], "name": Path(doc["id"]).name, "title": title(doc),
                        "kind": "file", "size": len(doc["markdown"].encode()), "mtime": doc["mtime"]}
                       for project, docs in self.projects.items() if project not in self.files.roots for doc in docs
                       if not is_private(Path(doc["id"]).name)]
                      + self.files.list(), key=lambda doc: doc["project"])

    def read(self, project: str, document_id: str) -> dict[str, Any] | None:
        if project in self.files.roots:
            return self.files.read(project, document_id)
        doc = next((doc for doc in self.projects.get(project, []) if doc["id"] == document_id), None)
        if doc is None or is_private(Path(document_id).name):
            return None
        return {"project": project, "id": document_id, "name": Path(document_id).name, "kind": "file",
                "size": len(doc["markdown"].encode()), "mtime": doc["mtime"], "truncated": False,
                **render_markdown(doc["markdown"])}

    def read_asset(self, project: str, document_id: str, asset_path: str) -> tuple[str, bytes] | None:
        if project in self.files.roots:
            return self.files.read_asset(project, document_id, asset_path)
        return None  # the fixture's own library section is Markdown only


def title(doc: dict[str, Any]) -> str:
    heading = next((line[2:].strip() for line in doc["markdown"].splitlines()[:40] if line.startswith("# ")), None)
    return heading or Path(doc["id"]).stem
