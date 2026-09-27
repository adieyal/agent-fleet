"""Transactional workspace state."""

from __future__ import annotations

from typing import Protocol, ContextManager

from ..domain.projects import Registry
from ..domain.choices import Choices
from ..domain.building import capacity_of


class WorkspaceState:
    def __init__(self, record: dict) -> None:
        self.registry = Registry.from_config(record)
        self.choices = Choices(record)
        self.capacity = capacity_of(record)

    def snapshot(self) -> dict:
        return {"projects": self.registry.to_config(), "capacity": self.capacity,
                "focus": self.choices.focus_snapshot(), "floors": self.choices.floors_snapshot(),
                "shuttered": self.choices.shuttered_snapshot()}


class Repository(Protocol):
    def read(self) -> dict: ...
    def transaction(self, actor: str) -> ContextManager[WorkspaceState]: ...
