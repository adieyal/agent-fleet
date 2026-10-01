"""The audit trail is kept until someone prunes it explicitly, and reads back by subject."""

from datetime import datetime, timedelta, timezone

import pytest

from fleet import cli, composition

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch):
    store = composition.open_store()
    store.clock = lambda: NOW
    monkeypatch.setattr(cli, "open_store", lambda: store)
    return store


def record(store, when, subject):
    store.clock = lambda: when
    with store.unit_of_work() as work:
        work.record_change(subject, "", "x", "test")


def test_prune_without_yes_says_what_it_would_delete_and_deletes_nothing(store, capsys):
    record(store, NOW - timedelta(days=30), "old:1")
    record(store, NOW - timedelta(days=20), "old:2")
    record(store, NOW, "new:1")
    before = store.history_after(0)
    with pytest.raises(SystemExit) as exit:
        cli.main(["history", "prune", "--before", (NOW - timedelta(days=1)).date().isoformat()])
    assert exit.value.code == 2
    captured = capsys.readouterr()
    assert (f"Would delete 2 history entries, from {(NOW - timedelta(days=30)).isoformat()} "
            f"to {(NOW - timedelta(days=20)).isoformat()}") in captured.out
    assert "run again with --yes" in captured.err
    assert store.history_after(0) == before


def test_prune_with_yes_deletes_and_records_the_pruning(store, capsys):
    record(store, NOW - timedelta(days=30), "old:1")
    record(store, NOW, "new:1")
    cli.main(["history", "prune", "--before", "2026-09-01", "--yes", "--actor", "adi"])
    assert "Deleted 1 history entries" in capsys.readouterr().out
    rows = store.history_after(0)
    assert [(row["subject"], row["actor"]) for row in rows] == [("new:1", "test"), ("history", "adi")]
    assert rows[-1]["to"] == "pruned 1 entries before 2026-09-01T00:00:00+00:00"


def test_prune_with_nothing_older_deletes_nothing(store, capsys):
    record(store, NOW, "new:1")
    cli.main(["history", "prune", "--before", "2026-09-01", "--yes"])
    assert "nothing to delete" in capsys.readouterr().out
    assert len(store.history_after(0)) == 1


def test_prune_refuses_a_date_it_cannot_read(store, capsys):
    with pytest.raises(SystemExit):
        cli.main(["history", "prune", "--before", "last week", "--yes"])
    assert "not an ISO date or time" in capsys.readouterr().err
