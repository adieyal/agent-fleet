import pytest
from fleet.container import configured_container


def test_links_are_idempotent_persistent_and_do_not_change_work():
    store = configured_container().store()
    work = configured_container(store).work()
    item = work.add(project="p", title="Task", goal="Ship", actor="user")
    criterion = work.add_criterion(item.id, text="Accept", verification="accepted", actor="user")
    execution = configured_container(store).execution()
    run = execution.link("host", "job", item.id, actor="user")
    history = store.history_after(0)
    assert execution.link("host", "job", item.id, actor="user") == run
    assert store.history_after(0) == history
    assert len(execution.actions()) == len(execution.runs()) == 1
    assert execution.actions()[0].source == "linked"
    assert execution.actions()[0].work_item == item.id
    assert run.status == "unknown outcome"
    assert run.runtime is run.start is run.end is run.last_observed is None
    entry = configured_container(store).library().link('https://example.org/report', work_item=item.id, actor='user')
    reopened = configured_container().store()
    assert configured_container(reopened).execution().runs() == [run]
    assert configured_container(reopened).library().list() == [entry]
    assert entry.project == "p" and entry.availability == "external"
    assert work.get(item.id) == item
    assert work.criteria(item.id) == [criterion]
    assert len(store.history_after(0)) == len(history) + 1
    other = work.add(project="p", title="Other", goal="Other", actor="user")
    with pytest.raises(ValueError, match="already linked"):
        execution.link("host", "job", other.id, actor="user")


def test_missing_work_creates_nothing():
    execution = configured_container().execution()
    with pytest.raises(LookupError):
        execution.link("host", "job", "missing", actor="user")
    assert execution.actions() == execution.runs() == []
