import pytest
import subprocess

from fleet.composition import open_attention, open_records, open_store, open_work


def test_blocker_atomic_deduplicated_resolved_and_reblocked(monkeypatch):
    store = open_store()
    work = open_work(store)
    attention = open_attention(store)
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
    assert open_work(open_store()).get(item.id).condition == "blocked"


def test_relations_summaries_and_all_writes_have_history(tmp_path):
    store = open_store()
    repo = tmp_path / 'management'
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True, timeout=10)
    open_records(store).register('p', repo, actor='author')
    work = open_work(store)
    first = work.add(project="p", title="A", goal="A goal", actor="author")
    second = work.add(project="p", title="B", goal="B goal", actor="author")
    relation = work.relate(first.id, second.id, actor="author")
    summary = work.set_summary(first.id, purpose="Purpose", done="Done", doing="Doing",
                               next="Next", authoring_role="orchestrator", actor="author")
    reopened = open_work(open_store())
    assert reopened.relations(first.id) == [relation]
    assert reopened.summary(first.id) == summary
    rows = store.history_after(0)
    assert len(rows) == 6
    assert all(row["actor"] == "author" for row in rows)
    assert all(row["to"] for row in rows)
    assert [row['subject'].split(':')[0] for row in rows] == ['workspace', 'work', 'work', 'work', 'records', 'records']
