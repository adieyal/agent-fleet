"""Public workspace commands and detached read snapshots."""

from __future__ import annotations

from typing import Callable, TypeVar

from .application import Repository
from .application.workspace import WorkspaceApplication
from .domain.projects import Registry

T = TypeVar("T")


class WorkspaceFacade:
    def __init__(self, repository: Repository, actor: str = "user") -> None:
        self.application = WorkspaceApplication(repository, actor)

    def snapshot(self) -> dict:
        return self.application.snapshot()

    def registry(self) -> Registry:
        return self.application.registry()

    def capacity(self) -> int:
        return self.application.capacity()

    def set_capacity(self, capacity: int) -> None:
        self.application.set_capacity(capacity)

    def edit_registry(self, change: Callable[[Registry], T]) -> T:
        return self.application.edit_registry(change)

    def settle(self) -> None:
        self.application.settle()

    def focus_snapshot(self) -> dict:
        return self.application.focus_snapshot()

    def floors_snapshot(self) -> dict:
        return self.application.floors_snapshot()

    def shuttered_snapshot(self) -> dict:
        return self.application.shuttered_snapshot()

    def focus_of(self, item: dict) -> str:
        return self.application.focus_of(item)

    def annotate(self, item: dict) -> dict:
        return self.application.annotate(item)

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        self.application.set_focus(focus, projects, labels)

    def move_in(self, hosts: list[str], label: str, shutter: str | None = None,
                name: str | None = None) -> dict:
        return self.application.move_in(hosts, label, shutter, name)

    def link_in(self, project_id: str, hosts: list[str], label: str) -> dict:
        return self.application.link_in(project_id, hosts, label)

    def merge(self, keep: str, other: str) -> dict:
        return self.application.merge(keep, other)

    def shutter(self, project_id: str) -> dict:
        return self.application.shutter(project_id)

    def restore(self, project_id: str, shutter: str | None = None) -> dict:
        return self.application.restore(project_id, shutter)
