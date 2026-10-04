"""The audit trail is kept until someone prunes it explicitly, and reads back by subject."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from fleet import cli, composition

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


@pytest.fixture
def store(cli_container):
    store = composition.open_store()
    store.clock = lambda: NOW
    cli_container.store.override(store)
    return store


def record(store, when, subject):
    store.clock = lambda: when
    with store.unit_of_work() as work:
        work.record_change(subject, "", "x", "test")


def test_prune_without_yes_says_what_it_would_delete_and_deletes_nothing(store, capsys, cli_container):
    record(store, NOW - timedelta(days=30), "old:1")
    record(store, NOW - timedelta(days=20), "old:2")
    record(store, NOW, "new:1")
    before = store.history_after(0)
    with pytest.raises(SystemExit) as exit:
        cli.main(["history", "prune", "--before", (NOW - timedelta(days=1)).date().isoformat()], container=cli_container)
    assert exit.value.code == 2
    captured = capsys.readouterr()
    assert (f"Would delete 2 history entries, from {(NOW - timedelta(days=30)).isoformat()} "
            f"to {(NOW - timedelta(days=20)).isoformat()}") in captured.out
    assert "run again with --yes" in captured.err
    assert store.history_after(0) == before


def test_prune_with_yes_deletes_and_records_the_pruning(store, capsys, cli_container):
    record(store, NOW - timedelta(days=30), "old:1")
    record(store, NOW, "new:1")
    cli.main(["history", "prune", "--before", "2026-09-01", "--yes", "--actor", "adi"], container=cli_container)
    assert "Deleted 1 history entries" in capsys.readouterr().out
    rows = store.history_after(0)
    assert [(row["subject"], row["actor"]) for row in rows] == [("new:1", "test"), ("history", "adi")]
    assert rows[-1]["to"] == "pruned 1 entries before 2026-09-01T00:00:00+00:00"


def test_prune_with_nothing_older_deletes_nothing(store, capsys, cli_container):
    record(store, NOW, "new:1")
    cli.main(["history", "prune", "--before", "2026-09-01", "--yes"], container=cli_container)
    assert "nothing to delete" in capsys.readouterr().out
    assert len(store.history_after(0)) == 1


def test_prune_refuses_a_date_it_cannot_read(store, capsys, cli_container):
    with pytest.raises(SystemExit):
        cli.main(["history", "prune", "--before", "last week", "--yes"], container=cli_container)
    assert "not an ISO date or time" in capsys.readouterr().err


@pytest.fixture
def outside_a_job(monkeypatch):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)


def show(capsys, *arguments: str) -> str:
    capsys.readouterr()
    cli.main(["history", *arguments])
    return capsys.readouterr().out


def test_a_work_item_reads_newest_first_with_field_changes_and_its_criteria(project_id, outside_a_job, capsys):
    work = composition.open_work()
    item = work.add(project=project_id, title="Audit", goal="Keep history", actor="user")
    work.set(item.id, actor="claude", next_step="Write the reader")
    criterion = work.add_criterion(item.id, text="Prune asks first", verification="judged", actor="claude")
    lines = show(capsys, "--subject", item.id[:8]).splitlines()
    assert lines[0] == f"History of work item {item.id}, newest first"
    assert lines[1].endswith(f"claude  no recorded run (criterion {criterion.id[:8]})")
    assert "  text: — → Prune asks first" in lines
    second = lines.index(next(line for line in lines[2:] if not line.startswith("  ")))
    assert lines[second].endswith("claude  no recorded run")
    assert lines[second + 1] == "  next_step: — → Write the reader"
    created = lines.index(next(line for line in lines if line.endswith("user  no recorded run")))
    assert {"  title: — → Audit", "  goal: — → Keep history", "  kind: — → task"} <= set(lines[created:])


def test_history_names_the_run_whose_job_made_the_change(project_id, monkeypatch, capsys):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)
    work = composition.open_work()
    item = work.add(project=project_id, title="Audit", goal="Keep history", actor="user")
    run = composition.open_execution().dispatch(item.id, host="h", runtime="codex", actor="user", reason="Go",
        idempotency_key="job-1", remote_job_id="job-1", guidance=None,
        payload=dict(cwd="/repo", arguments=[], steps=[dict(prompt="Go")], context=None, hold=False)).run
    monkeypatch.setenv("FLEET_JOB_ID", "job-1")
    composition.open_work().set(item.id, actor="claude", condition="ready for review")
    monkeypatch.setenv("FLEET_JOB_ID", "job-unknown-here")
    composition.open_work().set(item.id, actor="claude", next_step="Ship")
    capsys.readouterr()
    cli.main(["history", "--subject", f"work:item:{item.id}", "--json"])
    entries = json.loads(capsys.readouterr().out)["entries"]
    assert [(entry["job"], entry["source_run"]) for entry in entries[:2]] == [
        ("job-unknown-here", None), ("job-1", run.id)]
    text = show(capsys, "--subject", item.id)
    assert f"claude  run {run.id[:8]}" in text and "claude  job job-unkn" in text


def test_an_attention_item_shows_its_state_changes(project_id, outside_a_job, capsys):
    attention = composition.open_attention()
    item = attention.raise_item(project=project_id, kind="decision", owner="user", source="claude",
                                source_reference="ref-1", headline="Pick one", context_reference="x", actor="claude")
    attention.acknowledge(item.id, actor="adi")
    lines = show(capsys, "--subject", f"attention:{item.id[:6]}").splitlines()
    assert lines[0] == f"History of attention {item.id}, newest first"
    assert lines[1].endswith("adi  no recorded run") and lines[2] == "  state: open → acknowledged"
    assert lines[3].endswith("claude  no recorded run") and lines[4] == "  state: — → open"


def test_a_project_shows_only_its_own_workspace_changes(project_id, outside_a_job, capsys):
    composition.open_workspace().edit_registry(lambda registry: registry.create("other"))
    cli.main(["project", "rename", project_id, "renamed"])
    lines = show(capsys, "--subject", project_id).splitlines()
    assert lines[0] == f"History of project {project_id}, newest first"
    assert lines[2] == "  name: p → renamed"
    assert len([line for line in lines if not line.startswith("  ")]) == 3


def test_since_hides_older_changes(project_id, outside_a_job, capsys):
    item = composition.open_work().add(project=project_id, title="Audit", goal="Keep history", actor="user")
    assert "No changes recorded in that period." in show(capsys, "--subject", item.id, "--since", "2999-01-01")
    assert "title: — → Audit" in show(capsys, "--subject", item.id, "--since", "1h")


def test_an_ambiguous_or_unknown_subject_is_refused(project_id, outside_a_job, capsys):
    work = composition.open_work()
    work.add(project=project_id, title="One", goal="G", actor="user")
    work.add(project=project_id, title="Two", goal="G", actor="user")
    for reference, message in (("", "give a subject"), ("zzzz", "no history for 'zzzz'"),
                               ("work:item:", "matches several subjects")):
        with pytest.raises(SystemExit):
            cli.main(["history", "--subject", reference])
        assert message in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["history"])
    assert "give --subject" in capsys.readouterr().err
