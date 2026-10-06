"""Isolated recorded state, seeded through public module facades."""
from __future__ import annotations

from fleet.projections.live import fixture_document
from fleet.projections.attention import attention_items
import threading
from pathlib import Path
from typing import Any, Callable
from fleet.modules.workspace import Registry
from fleet.projections.workspace import resolve
from fleet.errors import FleetError
from fleet.services.documents import STATUS_LINE
from fleet.services.live import LiveWorkspace

class FixtureState(LiveWorkspace):
    """Same surface the HTTP handler uses on FleetState; only the user's choices ever change."""

    def __init__(self, fixture: dict[str, Any], *, container) -> None:
        self.fixture = fixture
        self.project_labels = fixture.get("project_labels", {})
        scope = container.fixture_scope()
        self.container = container = scope.container
        self.attention_directory = scope.directory
        store = container.store()
        self.store = store
        self.execution = container.execution()
        self.decisions = container.decisions()
        self.triage_status = container.triage_scheduler(deliver=None, host=None).status
        self.workspace = container.initialized_workspace(initial=fixture, actor="fixture-user")
        work = container.work()
        identities = {}
        for item in fixture.get("work_items", []):
            parent = identities[item["parent"]] if item["parent"] is not None else None
            identities[item["key"]] = work.add(project=item["project"], title=item["title"],
                goal=item["goal"], kind=item["kind"], parent=parent, actor="fixture-user").id
        self.registry = self.workspace.registry()
        self.capacity = self.workspace.capacity()
        self.attention = container.initialized_attention(
                                        workspace_path=Path(self.attention_directory.name) / "workspace.json")
        self.reads = container.live_readers(workspace=self.workspace, attention=self.attention)
        self.woken_until = 0.0
        for host in fixture["hosts"]:
            self.attention.observe({**host,
                "jobs": [resolve(self.registry, host["name"], job) for job in host["jobs"]],
                "sessions": [resolve(self.registry, host["name"], session) for session in host["sessions"]]})
        self.changed = threading.Condition()
        self.version = 0
        # The fixture has no responder, but readers of the live stream still ask for typing state.
        self.typing_seq = 0
        self.typing = {}
        self.pipeline_config = fixture.get("pipelines", {})
        self.pipeline_runs = {(report["host"], report["pipeline"]): {"run": report.get("run"),
                                                                     "baseline": report.get("baseline"), "seq": 1}
                              for report in fixture.get("pipeline_reports", [])}
        self.pipeline_seq = 1 if self.pipeline_runs else 0
        self.documents = container.project_documents()
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

    def clock(self) -> float:
        return self.fixture["time"]

    def job_hosts(self) -> dict[str, tuple[bool, set[str]]]:
        return {host["name"]: (bool(host.get("ok")), {job["id"] for job in host["jobs"]}) for host in self.fixture["hosts"]}

    @classmethod
    def load(cls, path, *, container):
        return cls(container.fixture_data(path=path), container=container)

    def host_names(self) -> list[str]:
        return [host["name"] for host in self.fixture["hosts"]]

    def live_jobs(self) -> dict[tuple[str, str], dict[str, Any]]:
        return {(host["name"], job["id"]): job for host in self.fixture["hosts"] for job in host["jobs"]}

    def move_on_host(self, host_name: str, identity: str, label: str) -> None:
        host = next(host for host in self.fixture["hosts"] if host["name"] == host_name)
        agents = [agent for agent in host["jobs"] + host["sessions"] if agent["id"].startswith(identity)]
        if len(agents) != 1:
            raise LookupError(f"no agent '{identity}' on {host_name}")
        agents[0]["project"] = label

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
            items = attention_items(self.reads.attention, self.fixture["hosts"])
            return fixture_document(self, self.triage_statuses(items), items)

    def pipeline_updates(self, after: int) -> list[dict[str, Any]]:
        return self.pipelines(self.registry, {host["name"]: host for host in self.fixture["hosts"]}, after)

    def known_projects(self) -> dict[str, Any]:
        self.registry = self.workspace.registry()
        return self.registry.projects

    def read_document(self, host_name: str, job_id: str, document_id: str) -> dict[str, Any]:
        """What /api/doc returns for a live host: the job's document entry plus raw Markdown."""
        host = next(host for host in self.fixture["hosts"] if host["name"] == host_name)
        job = next((job for job in host["jobs"] if job["id"] == job_id), None)
        entry = next((doc for doc in (job or {}).get("documents", []) if doc["id"] == document_id), None)
        markdown = self.fixture.get("job_documents", {}).get(f"{host_name}/{job_id}/{document_id}")
        if job is None or entry is None or markdown is None:
            raise FleetError(f"job {job_id} has no document {document_id}")
        return {**entry, "truncated": False, "job": job_id, "project": job["project"], "agent": job["agent"],
                "job_description": job["description"], "host": host_name,
                "content": STATUS_LINE.sub("", markdown).strip()}

    def read_asset(self, host_name: str, job_id: str, document_id: str, asset_path: str) -> tuple[str, bytes]:
        raise FleetError(f"job {job_id} has no asset {asset_path}")  # recorded fleets carry no images
