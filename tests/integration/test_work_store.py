import pytest
import subprocess
from fleet.container import configured_container


def test_blocker_atomic_deduplicated_resolved_and_reblocked(monkeypatch):
    store = configured_container().store()
    work = configured_container(store).work()
    attention = configured_container(store).initialized_attention()
    item = work.add(project="p1", title="Task", goal="Ship", actor="user")
    for _ in range(2):
        work.set(item.id, condition="blocked", actor="user")
    blocker, = attention.list()
    assert (blocker.kind, blocker.work_item, blocker.state) == ("blocker", item.id, "open")
    work.set(item.id, condition="none", actor="user")
    assert attention.get(blocker.id).state == "resolved"
    work.set(item.id, condition="blocked", actor="user")
    assert len(attention.list()) == 1
    assert attention.get(blocker.id).state == "open"
    before = store.history_after(0)
    original = work.repository.save

    def fail(self, *args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(type(work.repository), "save", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        work.set(item.id, condition="none", actor="user")
    assert attention.get(blocker.id).state == "open"
    assert store.history_after(0) == before
    monkeypatch.setattr(type(work.repository), "save", original.__func__)
    assert configured_container(configured_container().store()).work().get(item.id).condition == "blocked"


def test_relations_summaries_and_all_writes_have_history(tmp_path):
    store = configured_container().store()
    repo = tmp_path / 'management'
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True, timeout=10)
    configured_container(store).records().register('p', repo, actor='author')
    work = configured_container(store).work()
    first = work.add(project="p", title="A", goal="A goal", actor="author")
    second = work.add(project="p", title="B", goal="B goal", actor="author")
    relation = work.relate(first.id, second.id, actor="author")
    summary = work.set_summary(first.id, purpose="Purpose", done="Done", doing="Doing",
                               next="Next", authoring_role="orchestrator", actor="author")
    reopened = configured_container(configured_container().store()).work()
    assert reopened.relations(first.id) == [relation]
    assert reopened.summary(first.id) == summary
    rows = store.history_after(0)
    assert len(rows) == 6
    assert all(row["actor"] == "author" for row in rows)
    assert all(row["to"] for row in rows)
    assert [row['subject'].split(':')[0] for row in rows] == ['workspace', 'work', 'work', 'work', 'records', 'records']


def test_an_item_can_be_superseded_by_another_and_unknown_relation_types_are_refused(tmp_path):
    store = configured_container().store()
    repo = tmp_path / 'management'
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True, timeout=10)
    configured_container(store).records().register('p', repo, actor='author')
    work = configured_container(store).work()
    old = work.add(project="p", title="Old", goal="Old goal", actor="author")
    new = work.add(project="p", title="New", goal="New goal", actor="author")
    relation = work.relate(old.id, new.id, type="superseded-by", actor="author")
    assert configured_container(configured_container().store()).work().relations_by_item() == {old.id: [relation]}
    with pytest.raises(ValueError, match="unknown relation type"):
        work.relate(old.id, new.id, type="replaces", actor="author")
