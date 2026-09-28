from contextlib import contextmanager
from copy import deepcopy

import pytest

from fleet.modules.workspace import WorkspaceFacade, WorkspaceState, WorkspaceSnapshot, NoVacancy


class MemoryRepository:
    def __init__(self):
        self.record = WorkspaceState(WorkspaceSnapshot()).snapshot()

    def read(self):
        return deepcopy(self.record)

    @contextmanager
    def transaction(self, actor):
        state = WorkspaceState(self.read())
        yield state
        self.record = state.snapshot()


def test_capacity_and_stable_floor_assignment_with_fake_port():
    workspace = WorkspaceFacade(MemoryRepository())
    ids = [workspace.move_in(["host"], str(index)).project_id for index in range(6)]
    assert workspace.capacity() == 6
    before = workspace.snapshot()
    with pytest.raises(NoVacancy):
        workspace.move_in(["host"], "overflow")
    assert workspace.snapshot() == before
    workspace.set_capacity(10)
    more = [workspace.move_in(["host"], str(index)).project_id for index in range(6, 10)]
    assert workspace.floors_snapshot() == dict(zip(ids + more, range(1, 11)))
    workspace.shutter(ids[2])
    workspace.restore(ids[2])
    workspace.set_capacity(2)
    workspace.set_capacity(10)
    assert workspace.floors_snapshot() == dict(zip(ids + more, range(1, 11)))
