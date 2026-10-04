import json
from datetime import datetime, timedelta, timezone

from fleet.container import configured_container
from fleet import cli

from fleet.modules.execution import JobObservation
from fleet.infrastructure.sqlite.migrations import MIGRATIONS
from fleet.infrastructure.sqlite.store import connect
from contextlib import closing


def test_status_runs_library_and_later_next_step_survive_restart(capsys, project_id):
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    store = configured_container(clock=lambda : now[0]).store()
    work = configured_container(store).work()
    item = work.add(project=project_id, title="Milestone", goal="Deliver", kind="milestone",
                    next_step="Try again", actor="user")
    criterion = work.add_criterion(item.id, text="Approved", verification="accepted", actor="user")
    work.meet(criterion.id, actor="user")
    before = work.get(item.id), work.progress(item.id)
    execution = configured_container(store).execution()
    run = execution.link("host-a", "job", item.id, actor="user")
    execution.link("host-b", "other-job", item.id, actor="user")
    now[0] += timedelta(minutes=1)
    execution.observe("host-a", JobObservation("job", "failed", "codex", item.created, now[0], now[0]))
    configured_container(store).library().index_run(run=run.id, work_item=item.id, kind='trace', title='Trace', location='fleet://host-a/job/trace', availability='unavailable')
    assert (work.get(item.id), work.progress(item.id)) == before
    sequence = store.latest_sequence()
    cli.main(["status", "p", "--json"])
    node, = json.loads(capsys.readouterr().out)["work_items"]
    assert len(node["runs"]) == 2
    assert node["no_follow_up_yet"] is True
    cli.main(["status", "p"])
    output = capsys.readouterr().out
    for text in ("host-a", "host-b", "codex", "failed", "unavailable", "No follow-up yet",
                 "Start:", "End:", "Last observed:", "Reason: unknown", "1/1 criteria"):
        assert text in output
    assert store.latest_sequence() == sequence
    assert configured_container(store).initialized_attention().list() == []
    now[0] += timedelta(minutes=1)
    work.set(item.id, title="Renamed", actor="user")
    assert work.get(item.id).next_step_recorded_at == item.created
    work.set(item.id, next_step="Try again", actor="user")
    assert configured_container(configured_container().store()).work().get(item.id).next_step_recorded_at == now[0]
    cli.main(["status", "p", "--json"])
    assert json.loads(capsys.readouterr().out)["work_items"][0]["no_follow_up_yet"] is False


def test_existing_next_step_migrates_with_unknown_recording_time(tmp_path):
    path = tmp_path / "old.db"
    with closing(connect(path)) as connection:
        for statements in MIGRATIONS[:-1]:
            for statement in statements:
                connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        connection.execute("INSERT INTO work_item VALUES (?, ?)", ("old", json.dumps({
            "id": "old", "project": "p", "parent": None, "kind": "task", "title": "Old work",
            "goal": "Deliver", "condition": "none", "resume_condition": None,
            "next_step": "Retry", "focus": None, "created": "2026-09-26T00:00:00+00:00",
            "updated": "2026-09-27T00:00:00+00:00"})))
    work = configured_container(configured_container(path=path).store()).work()
    assert work.get("old").next_step == "Retry"
    assert work.get("old").next_step_recorded_at is None
