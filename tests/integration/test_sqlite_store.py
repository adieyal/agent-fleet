"""Controller transaction and cross-process history checks."""

import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from fleet.composition import open_store
from fleet.infrastructure.sqlite import store as sqlite_store
from fleet.web.server import FleetState, make_handler


def test_migrations_are_ordered_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "controller.db"
    with open_store(path) as store:
        assert store.schema_version() == 1
        with store.unit_of_work() as work:
            work.record_change("work:1", "ready", "active", "test")
    with open_store(path) as store:
        assert store.schema_version() == 1
        assert [(row["sequence"], row["subject"]) for row in store.history_after(0)] == [(1, "work:1")]


def test_store_path_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "custom" / "fleet.db"
    monkeypatch.setenv("FLEET_STORE", str(path))
    with open_store() as store:
        assert store.path == path
    assert path.is_file()


def test_pending_migrations_run_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "controller.db"
    open_store(path)
    monkeypatch.setattr(sqlite_store, "MIGRATIONS", sqlite_store.MIGRATIONS + (
        ("CREATE TABLE sample (value TEXT)",),
        ("INSERT INTO sample VALUES ('migrated')",),
    ))
    for _ in range(2):
        store = open_store(path)
        assert store.schema_version() == 3
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM sample").fetchall() == [("migrated",)]


def test_store_closes_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connections = []
    connect = sqlite_store.connect

    def track_connection(path: Path) -> sqlite3.Connection:
        connection = connect(path)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite_store, "connect", track_connection)
    store = open_store(tmp_path / "controller.db")
    store.schema_version()
    store.history_after(0)
    store.latest_sequence()
    try:
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


def test_rollback_includes_history(tmp_path: Path) -> None:
    with open_store(tmp_path / "controller.db") as store:
        with pytest.raises(RuntimeError):
            with store.unit_of_work() as work:
                work.connection.execute("CREATE TABLE sample (value TEXT)")
                work.connection.execute("INSERT INTO sample VALUES ('lost')")
                work.record_change("work:1", "ready", "active", "test")
                raise RuntimeError("abort")
        assert store.history_after(0) == []
        with sqlite3.connect(store.path) as connection:
            assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'sample'").fetchone() is None


def test_successful_write_requires_history(tmp_path: Path) -> None:
    with open_store(tmp_path / "controller.db") as store:
        with pytest.raises(ValueError, match="history entry"):
            with store.unit_of_work() as work:
                work.connection.execute("CREATE TABLE sample (value TEXT)")
                work.connection.execute("INSERT INTO sample VALUES ('no history')")
        with sqlite3.connect(store.path) as connection:
            assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'sample'").fetchone() is None
        with store.unit_of_work() as work:
            work.connection.execute("CREATE TABLE sample (value TEXT)")
            work.connection.execute("INSERT INTO sample VALUES ('saved')")
            work.record_change("sample:1", "absent", "saved", "test")
        assert len(store.history_after(0)) == 1


def test_history_and_retention(tmp_path: Path) -> None:
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    with open_store(tmp_path / "controller.db", clock=lambda: now) as store:
        with store.unit_of_work() as work:
            work.record_change("work:1", "ready", "active", "first")
            work.record_change("work:2", "ready", "active", "second")
        rows = store.history_after(0)
        assert [(row["sequence"], row["subject"], row["from"], row["to"], row["actor"])
                for row in rows] == [(1, "work:1", "ready", "active", "first"),
                                    (2, "work:2", "ready", "active", "second")]
        assert all(row["time"] == now.isoformat() for row in rows)
        store.clock = lambda: now + timedelta(days=7)
        with store.unit_of_work() as work:
            work.record_change("work:3", "ready", "active", "third")
        assert [row["sequence"] for row in store.history_after(0)] == [1, 2, 3]
        store.clock = lambda: now + timedelta(days=7, microseconds=1)
        with store.unit_of_work() as work:
            work.record_change("work:4", "ready", "active", "fourth")
        assert [row["sequence"] for row in store.history_after(0)] == [3, 4]


def test_two_processes_lose_no_write(tmp_path: Path) -> None:
    path = tmp_path / "controller.db"
    script = ("import sys\nfrom fleet.composition import open_store\n"
              "with open_store() as store:\n"
              "    with store.unit_of_work() as work:\n"
              "        work.record_change(sys.argv[1], 'ready', 'active', 'process')\n")
    env = {**os.environ, "FLEET_STORE": str(path)}
    processes = [subprocess.Popen([sys.executable, "-c", script, f"work:{index}"], env=env)
                 for index in range(2)]
    for process in processes:
        assert process.wait(timeout=20) == 0
    with open_store(path) as store:
        rows = store.history_after(0)
        assert {row["subject"] for row in rows} == {"work:0", "work:1"}
        assert [row["sequence"] for row in rows] == [1, 2]


def test_cli_process_change_refreshes_sse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "controller.db"
    monkeypatch.setenv("FLEET_STORE", str(path))
    with open_store() as store:
        state = FleetState([], store=store)
        stop = threading.Event()
        watcher = threading.Thread(target=state.follow_history, args=(stop,))
        watcher.start()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/stream", timeout=10) as stream:
                assert stream.readline() == b"event: state\n"
                assert stream.readline().startswith(b"data: ")
                assert stream.readline() == b"\n"
                baseline = state.version
                script = ("from fleet.cli import open_store\n"
                          "with open_store() as store:\n"
                          "    with store.unit_of_work() as work:\n"
                          "        work.record_change('work:1', 'ready', 'active', 'cli')\n")
                subprocess.run([sys.executable, "-c", script], env=os.environ.copy(), check=True, timeout=20)
                assert stream.readline() == b"event: state\n"
                assert state.version > baseline
        finally:
            stop.set()
            watcher.join(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
