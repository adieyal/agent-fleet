"""Transactional workspace state."""

from __future__ import annotations

from typing import Protocol, ContextManager, Callable

from ..domain.projects import Registry
from ..domain.choices import Choices
from ..domain.building import capacity_of
from ..domain.records import WorkspaceSnapshot


class WorkspaceState:
    def __init__(self, record: WorkspaceSnapshot,
                 rehome_records: Callable[[str, str], dict[str, int]] | None = None) -> None:
        self.rehome_records = rehome_records or (lambda keep, other: dict(work_items=0, attention=0, runs=0, decisions=0))
        self.registry = Registry(record.projects)
        self.choices = Choices(record)
        self.capacity = capacity_of(record.capacity)

    def snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(list(self.registry.projects.values()), self.capacity,
                                 self.choices.focus_snapshot(), self.choices.floors_snapshot(),
                                 self.choices.shuttered_snapshot())


class Repository(Protocol):
    def read(self) -> WorkspaceSnapshot: ...
    def transaction(self, actor: str) -> ContextManager[WorkspaceState]: ...
    def management_repository(self, project: str) -> str: ...
    def register_management_repository(self, project: str, path: str, actor: str) -> None: ...
