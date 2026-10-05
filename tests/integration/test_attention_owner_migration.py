import json
import sqlite3
from contextlib import closing


from fleet.container import configured_container
from fleet.infrastructure.sqlite.migrations import MIGRATIONS

OWNER_SPLIT = 17  # the migration that splits attention_item.owner into subject and owner


def old_store(path, owners):
    """A store at the schema just before the split, holding one open item per owner value."""
    with closing(sqlite3.connect(path)) as connection:
        for statements in MIGRATIONS[:OWNER_SPLIT - 1]:
            for statement in statements:
                connection.execute(statement)
        for number, owner in enumerate(owners):
            connection.execute(
                "INSERT INTO attention_item (id, project, kind, owner, source, source_reference, headline, "
                "context_reference, state, last_seen) VALUES (?, 'p1', 'blocker', ?, 'test', ?, 'Step failed', "
                "'doc', 'open', '2026-10-01T09:00:00+00:00')", (f"i{number}", owner, f"r{number}"))
        connection.execute(f"PRAGMA user_version = {OWNER_SPLIT - 1}")
        connection.commit()


def test_the_split_moves_job_session_and_run_references_to_subject_and_leaves_every_item_with_the_user(tmp_path):
    owners = ["job:carbon:ab12", "session:home:s1", "run:r9", "user"]
    old_store(tmp_path / "store.db", owners)

    store = configured_container(path=tmp_path / 'store.db').store()
    items = {item.id: item for item in configured_container(store).initialized_attention(workspace_path=tmp_path / 'missing.json').list()}

    assert store.schema_version() >= OWNER_SPLIT
    assert [(items[f"i{n}"].owner, items[f"i{n}"].subject) for n in range(len(owners))] == [
        ("user", "job:carbon:ab12"), ("user", "session:home:s1"), ("user", "run:r9"), ("user", None)]
    assert all((item.owner_reason, item.owner_actor, item.owner_at) == (None, None, None) for item in items.values())


def test_version_16_upgrade_preserves_p2_observations_and_p3_history(tmp_path):
    path = tmp_path / "store.db"
    old_store(path, ["job:carbon:ab12"])
    run = {"id": "r1", "status": "running"}
    observation = {"last_observed": "2026-10-01T09:00:00+00:00", "usage": {"tokens": 42}}
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 16
        connection.execute("INSERT INTO execution_action (id, record) VALUES ('a1', '{}')")
        connection.execute("INSERT INTO execution_run (id, action, host, remote_job_id, record) "
                           "VALUES (?, 'a1', 'carbon', 'job1', ?)", ("r1", json.dumps(run)))
        connection.execute("INSERT INTO execution_run_observation (run, record) VALUES (?, ?)",
                           ("r1", json.dumps(observation)))
        connection.execute('INSERT INTO state_history (subject, "from", "to", actor, time, job) '
                           "VALUES ('execution:run:r1', 'pending', 'running', 'codex', 'now', 'job1')")
        connection.commit()
    store = configured_container(path=path).store()
    assert OWNER_SPLIT == 17
    # Page annotation is the next additive migration; require its exact schema as well.
    assert store.schema_version() == 18
    with closing(sqlite3.connect(path)) as connection:
        assert json.loads(connection.execute("SELECT record FROM execution_run WHERE id='r1'").fetchone()[0]) == run
        assert json.loads(connection.execute("SELECT record FROM execution_run_observation WHERE run='r1'").fetchone()[0]) == observation
        assert connection.execute("SELECT job FROM state_history WHERE subject='execution:run:r1'").fetchone()[0] == "job1"
        assert connection.execute("SELECT count(*) FROM triage_scheduler").fetchone()[0] == 0
        assert connection.execute("SELECT owner, subject, page_annotation FROM attention_item").fetchone() == ("user", "job:carbon:ab12", None)
    assert configured_container(path=path).store().schema_version() == 18
