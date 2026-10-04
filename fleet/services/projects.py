"""Project registry workflows and worker observations."""
from __future__ import annotations

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
