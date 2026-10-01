import sqlite3
from contextlib import closing

from fleet.composition import open_attention, open_store
from fleet.infrastructure.sqlite.migrations import MIGRATIONS

OWNER_SPLIT = 15  # the migration that splits attention_item.owner into subject and owner


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

    store = open_store(tmp_path / "store.db")
    items = {item.id: item for item in open_attention(store, workspace_path=tmp_path / "missing.json").list()}

    assert store.schema_version() >= OWNER_SPLIT
    assert [(items[f"i{n}"].owner, items[f"i{n}"].subject) for n in range(len(owners))] == [
        ("user", "job:carbon:ab12"), ("user", "session:home:s1"), ("user", "run:r9"), ("user", None)]
    assert all((item.owner_reason, item.owner_actor, item.owner_at) == (None, None, None) for item in items.values())
