"""Public workspace commands and detached read snapshots."""

from __future__ import annotations

from typing import Callable, TypeVar

from .application import Repository
from .application.workspace import WorkspaceApplication
from .domain.projects import Registry
from .domain.records import Focus, Shuttered, WorkspaceSnapshot, ProjectReference, Placement, MergeResult

T = TypeVar("T")


class WorkspaceFacade:
    def __init__(self, repository: Repository, actor: str = "user") -> None:
        self.application = WorkspaceApplication(repository, actor)

    def snapshot(self) -> WorkspaceSnapshot:
        return self.application.snapshot()

    def register_management_repository(self, project: str, path: str, *, actor: str) -> None:
        if not project.strip() or not actor.strip():
            raise ValueError('project and actor are required')
        self.application.repository.register_management_repository(project, path, actor)

    def management_repository(self, project: str) -> str:
        return self.application.repository.management_repository(project)

    def registry(self) -> Registry:
        return self.application.registry()

    def resolve_project(self, reference: str) -> str:
        return self.registry().resolve(reference)

    def capacity(self) -> int:
        return self.application.capacity()

    def set_capacity(self, capacity: int) -> None:
        self.application.set_capacity(capacity)

    def edit_registry(self, change: Callable[[Registry], T]) -> T:
        return self.application.edit_registry(change)

    def settle(self) -> None:
        self.application.settle()

    def focus_snapshot(self) -> Focus:
        return self.application.focus_snapshot()

    def floors_snapshot(self) -> dict[str, int]:
        return self.application.floors_snapshot()

    def shuttered_snapshot(self) -> dict[str, Shuttered]:
        return self.application.shuttered_snapshot()

    def require_claims_allowed(self, project: str, host: str) -> None:
        linked = self.registry().project_for(host, project)
        shuttered = self.shuttered_snapshot()
        if project in shuttered or (linked is not None and linked.id in shuttered):
            identity = project if project in shuttered else linked.id
            raise ValueError(f"project '{project}' is shuttered (in the deck's storehouse), so no work can start in "
                             f"it; restore it with: fleet project restore {identity}, or from the deck")

    def focus_of(self, item: ProjectReference) -> str:
        return self.application.focus_of(item)

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        self.application.set_focus(focus, projects, labels)

    def move_in(self, hosts: list[str], label: str, shutter: str | None = None,
                name: str | None = None) -> Placement:
        return self.application.move_in(hosts, label, shutter, name)

    def link_in(self, project_id: str, hosts: list[str], label: str) -> Placement:
        return self.application.link_in(project_id, hosts, label)

    def merge(self, keep: str, other: str) -> MergeResult:
        return self.application.merge(keep, other)

    def shutter(self, project_id: str) -> Placement:
        return self.application.shutter(project_id)

    def restore(self, project_id: str, shutter: str | None = None) -> Placement:
        return self.application.restore(project_id, shutter)
