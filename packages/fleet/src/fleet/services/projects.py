"""Project registry workflows and worker observations."""
from __future__ import annotations
from dataclasses import asdict
from typing import Any


from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fleet.modules import workspace as projects
from fleet.transport import FleetError, Host


class Projects:
    def __init__(self, workspace, execution, records, documents, transport):
        self.workspace, self.execution, self.records = workspace, execution, records
        self.documents, self.transport = documents, transport

    def parse_link(self, text: str) -> tuple[str, str]:
        """`host:label` → (host, label) for a configured host."""
        host, _, label = text.partition(":")
        if not host or not label:
            raise FleetError(f"expected host:label, got '{text}'")
        return self.transport.host_by_name(host).name, label

    def observed_labels(self, registry: projects.Registry, hosts: list[Host]):
        """(host, label, remote) for unlinked labels in jobs and sessions, plus per-host errors."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = pool.submit(self.transport.gather, hosts, ["ls", "--all"])
            sessions = pool.submit(self.transport.gather_sessions, hosts)
            reports, by_host = jobs.result(), sessions.result()
        errors = [report.error for report in reports if report.error]
        directories: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for report in reports:
            for item in report.jobs + by_host.get(report.host.name, []):
                label, directory = item.get("project"), item.get("cwd")
                if label and directory and registry.project_for(report.host.name, label) is None:
                    directories[report.host.name][directory].add(label)

        def remotes(host: Host) -> list[tuple[str, str, str]]:
            found = self.transport.repository_remotes(host, sorted(directories[host.name]))
            return [(host.name, label, url) for directory, urls in found.items()
                    for label in sorted(directories[host.name][directory]) for url in urls]

        observed = []
        for host in hosts:
            if directories.get(host.name):
                try:
                    observed += remotes(host)
                except FleetError as error:
                    errors.append(str(error))
        return observed, errors

    def add(self, name: str, repositories: list[str], links: list[str]):
        linked = [self.parse_link(text) for text in links]
        def create(registry):
            project = registry.create(name, repositories)
            for host, label in linked:
                registry.link(project.id, host, label)
            return project
        return self.workspace().edit_registry(create)

    def rename(self, reference: str, name: str):
        workspace = self.workspace()
        identity = workspace.resolve_project(reference)
        workspace.edit_registry(lambda registry: registry.rename(identity, name))
        return workspace.registry().get(identity)

    def link(self, reference: str, text: str):
        workspace = self.workspace()
        identity = workspace.resolve_project(reference)
        host, label = self.parse_link(text)
        link = workspace.edit_registry(lambda registry: registry.link(identity, host, label))
        count = self.execution.assign_label(host, label, identity, actor="user")
        moved = self.documents().assign_label(host, label, identity)
        return identity, link, count, moved

    def unlink(self, text: str):
        host, _, label = text.partition(":")
        identity = self.workspace().edit_registry(lambda registry: registry.unlink(host, label))
        return host, label, identity

    def merge(self, keep: str, other: str):
        workspace = self.workspace()
        keep, other = workspace.resolve_project(keep), workspace.resolve_project(other)
        previous = workspace.registry().get(other)
        result = workspace.merge(keep, other)
        return previous, workspace.registry().get(keep), result

    def management(self, reference: str, path: str, *, actor: str):
        identity = self.workspace().resolve_project(reference)
        root = Path(path).resolve()
        try:
            moved = self.records.register(identity, root, actor=actor)
        except (ValueError, OSError) as error:
            raise FleetError(str(error)) from error
        return identity, root, moved

    def repository(self, reference: str, url: str, *, remove: bool = False):
        workspace = self.workspace()
        identity = workspace.resolve_project(reference)
        operation = 'remove_repository' if remove else 'add_repository'
        workspace.edit_registry(lambda registry: getattr(registry, operation)(identity, url))
        return identity

    def restore(self, reference: str, shutter: str | None):
        workspace = self.workspace()
        identity = workspace.resolve_project(reference)
        shutter = workspace.resolve_project(shutter) if shutter is not None else None
        try:
            placement = workspace.restore(identity, shutter)
        except LookupError as error:
            raise FleetError(str(error)) from error
        return placement, workspace.registry().get(placement.project_id).name, shutter

    def suggestions(self, registry):
        observed, errors = self.observed_labels(registry, self.transport.configured_hosts())
        suggestions = list(dict.fromkeys((suggestion.link, suggestion.project_id)
                                         for suggestion in registry.suggest_links(observed)))
        return suggestions, errors


class LiveProjects:
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

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        self.workspace.set_focus(focus, projects, labels)
        self.bump()
