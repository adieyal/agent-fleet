from dataclasses import is_dataclass

import pytest

from fleet.composition import open_attention, open_store, open_work, open_workspace
from fleet.infrastructure.sqlite.repository import Repository
from fleet.infrastructure.sqlite.workspace import WorkspaceRepository
from fleet.modules.workspace import WorkspaceFacade


def test_workspace_uses_shared_repository():
    assert issubclass(WorkspaceRepository, Repository)


def test_workspace_boundary_records_are_typed():
    workspace = open_workspace()
    result = workspace.move_in(["host"], "demo")
    assert is_dataclass(result)
    assert is_dataclass(workspace.snapshot())
    assert is_dataclass(workspace.focus_snapshot())
    workspace.shutter(result.project_id)
    assert is_dataclass(workspace.shuttered_snapshot()[result.project_id])


def test_bound_workspace_and_library_rollback_together():
    from fleet.infrastructure.sqlite.library import LibraryRepository
    from fleet.modules.library import LibraryEntry

    store = open_store()
    workspace = open_workspace(store)
    before = workspace.snapshot()
    sequence = store.latest_sequence()
    with pytest.raises(RuntimeError, match="after writes"):
        with store.unit_of_work() as unit:
            bound = WorkspaceFacade(WorkspaceRepository(store, unit))
            bound.move_in(["host"], "demo")
            bound.register_management_repository("demo", "/management", actor="user")
            assert bound.management_repository("demo") == "/management"
            LibraryRepository(store, unit).save(LibraryEntry(
                id="entry", project="demo", work_item=None, run=None, kind="link",
                title="Report", source="user", canonical_location="https://example.org", availability="external",
                current=True), "user")
            raise RuntimeError("after writes")
    assert workspace.snapshot() == before
    assert LibraryRepository(store).list() == []
    with pytest.raises(ValueError, match="not registered"):
        workspace.management_repository("demo")
    assert store.latest_sequence() == sequence


def test_block_failure_after_attention_write_rolls_back(monkeypatch):
    store = open_store()
    work = open_work(store)
    attention = open_attention(store)
    item = work.add(project="demo", title="Task", goal="Ship", actor="user")
    sequence = store.latest_sequence()

    def fail(repository, *args):
        assert repository.attention.list()[0].work_item == item.id
        raise RuntimeError("after attention write")

    monkeypatch.setattr(type(work.repository), "save", fail)
    with pytest.raises(RuntimeError, match="after attention write"):
        work.set(item.id, condition="blocked", actor="user")
    assert work.get(item.id) == item
    assert attention.list() == []
    assert store.latest_sequence() == sequence
