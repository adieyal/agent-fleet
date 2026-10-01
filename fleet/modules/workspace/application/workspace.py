"""Workspace commands execute against one current transactional aggregate."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from fleet.errors import FleetError
from . import Repository, WorkspaceState
from ..domain.projects import Registry
from ..domain.records import Focus, Shuttered, WorkspaceSnapshot, ProjectReference, Placement, MergeResult
from ..domain.choices import Choices, NotShuttered, AlreadyHoused
from ..domain.building import capacity_of

T = TypeVar("T")


class WorkspaceApplication:
    def __init__(self, repository: Repository, actor: str = "user") -> None:
        self.repository = repository
        self.actor = actor

    def snapshot(self) -> WorkspaceSnapshot:
        return self.repository.read()

    def registry(self) -> Registry:
        return Registry(self.snapshot().projects)

    def capacity(self) -> int:
        return self.snapshot().capacity

    def set_capacity(self, capacity: int) -> None:
        value = capacity_of(capacity)
        with self.repository.transaction(self.actor) as state:
            state.capacity = value

    def edit_registry(self, change: Callable[[Registry], T]) -> T:
        with self.repository.transaction(self.actor) as state:
            previous = set(state.registry.projects)
            result = change(state.registry)
            for removed in previous - state.registry.projects.keys():
                state.choices.forget_project(removed)
            state.choices.settle(state.registry.projects, state.capacity)
            return result

    def settle(self) -> None:
        with self.repository.transaction(self.actor) as state:
            state.choices.settle(state.registry.projects, state.capacity)

    def focus_snapshot(self) -> Focus:
        return self.snapshot().focus

    def floors_snapshot(self) -> dict[str, int]:
        return self.snapshot().floors

    def shuttered_snapshot(self) -> dict[str, Shuttered]:
        return self.snapshot().shuttered

    def focus_of(self, item: ProjectReference) -> str:
        return Choices(self.snapshot()).focus_of(item)

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        with self.repository.transaction(self.actor) as state:
            state.choices.set_focus(focus, projects, labels, state.registry.projects)

    @staticmethod
    def _make_room(state: WorkspaceState, shutter: str | None) -> None:
        state.choices.settle(state.registry.projects, state.capacity)
        if shutter is not None:
            if state.choices.floors.get(shutter, state.capacity + 1) > state.capacity:
                raise FleetError(f"{shutter} holds no floor to clear")
            state.choices.shutter(shutter, time.time())
        state.choices.free_floor(state.capacity)

    def move_in(self, hosts: list[str], label: str, shutter: str | None = None,
                name: str | None = None) -> Placement:
        with self.repository.transaction(self.actor) as state:
            if not hosts or not label:
                raise FleetError("hosts and a label are required")
            for host in hosts:
                if state.registry.project_for(host, label):
                    raise AlreadyHoused(f"{host}:{label} already belongs to a project")
            self._make_room(state, shutter)
            project = state.registry.create(label if name is None else name)
            for host in hosts:
                state.registry.link(project.id, host, label)
            floor = state.choices.move_in(project.id, state.capacity)
            return Placement(project.id, floor)

    def link_in(self, project_id: str, hosts: list[str], label: str) -> Placement:
        with self.repository.transaction(self.actor) as state:
            state.registry.get(project_id)
            for host in hosts:
                if state.registry.project_for(host, label):
                    raise AlreadyHoused(f"{host}:{label} already belongs to a project")
                state.registry.link(project_id, host, label)
            return Placement(project_id, state.choices.floors.get(project_id))

    def merge(self, keep: str, other: str) -> MergeResult:
        with self.repository.transaction(self.actor) as state:
            state.registry.merge(keep, other)
            freed = state.choices.forget_project(other)
            counts = state.rehome_records(keep, other)
            return MergeResult(keep, other, freed, state.choices.floors.get(keep), counts)

    def shutter(self, project_id: str) -> Placement:
        with self.repository.transaction(self.actor) as state:
            state.registry.get(project_id)
            return Placement(project_id, state.choices.shutter(project_id, time.time()))

    def restore(self, project_id: str, shutter: str | None = None) -> Placement:
        with self.repository.transaction(self.actor) as state:
            state.registry.get(project_id)
            if project_id not in state.choices.shuttered:
                raise NotShuttered(f"{project_id} is not in the storehouse")
            self._make_room(state, shutter)
            return Placement(project_id, state.choices.restore(project_id, state.capacity))
