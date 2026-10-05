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


from fleet.container import configured_container
from fleet.infrastructure.sqlite import store as sqlite_store
from fleet_web.server import FleetState, make_handler


def test_migrations_are_ordered_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "controller.db"
    store = configured_container(path=path).store()
    assert store.schema_version() == len(sqlite_store.MIGRATIONS)
    with store.unit_of_work() as work:
        work.record_change("work:1", "ready", "active", "test")
    store = configured_container(path=path).store()
    assert store.schema_version() == len(sqlite_store.MIGRATIONS)
    assert [(row["sequence"], row["subject"]) for row in store.history_after(0)] == [(1, "work:1")]


def test_store_path_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "custom" / "fleet.db"
    monkeypatch.setenv("FLEET_STORE", str(path))
    store = configured_container().store()
    assert store.path == path
    assert path.is_file()


def test_pending_migrations_run_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "controller.db"
    configured_container(path=path).store()
    monkeypatch.setattr(sqlite_store, "MIGRATIONS", sqlite_store.MIGRATIONS + (
        ("CREATE TABLE sample (value TEXT)",),
        ("INSERT INTO sample VALUES ('migrated')",),
    ))
    for _ in range(2):
        store = configured_container(path=path).store()
        assert store.schema_version() == len(sqlite_store.MIGRATIONS)
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
    store = configured_container(path=tmp_path / 'controller.db').store()
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
    store = configured_container(path=tmp_path / 'controller.db').store()
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
    store = configured_container(path=tmp_path / 'controller.db').store()
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


def test_history_is_kept_until_pruned_explicitly(tmp_path: Path) -> None:
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    store = configured_container(path=tmp_path / 'controller.db', clock=lambda : now).store()
    with store.unit_of_work() as work:
        work.record_change("work:1", "ready", "active", "first")
        work.record_change("work:2", "ready", "active", "second")
    rows = store.history_after(0)
    assert [(row["sequence"], row["subject"], row["from"], row["to"], row["actor"])
            for row in rows] == [(1, "work:1", "ready", "active", "first"),
                                (2, "work:2", "ready", "active", "second")]
    assert all(row["time"] == now.isoformat() for row in rows)
    store.clock = lambda: now + timedelta(days=400)
    with store.unit_of_work() as work:
        work.record_change("work:3", "ready", "active", "third")
    assert [row["sequence"] for row in store.history_after(0)] == [1, 2, 3]
    cutoff = now + timedelta(days=1)
    assert store.history_span_before(cutoff) == (2, now.isoformat(), now.isoformat())
    assert store.prune_history(cutoff, "user") == 2
    rows = store.history_after(0)
    assert [(row["sequence"], row["subject"], row["to"], row["actor"]) for row in rows] == [
        (3, "work:3", "active", "third"),
        (4, "history", f"pruned 2 entries before {cutoff.isoformat()}", "user")]
    assert store.history_span_before(cutoff) == (0, None, None)


def test_two_processes_lose_no_write(tmp_path: Path) -> None:
    path = tmp_path / "fleet.db"
    store = configured_container(path=path).store()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE counter (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO counter VALUES (0)")
    start = tmp_path / "start"
    writes = 2
    script = """
import sys
import time
from pathlib import Path
from fleet.container import configured_container

store = configured_container().store()
print('ready', flush=True)
while not Path(sys.argv[1]).exists():
    time.sleep(0.01)
for _ in range(int(sys.argv[2])):
    print('next', flush=True)
    assert sys.stdin.readline() == 'write\\n'
    with store.unit_of_work() as work:
        value = work.connection.execute('SELECT value FROM counter').fetchone()[0]
        time.sleep(0.001)
        work.connection.execute('UPDATE counter SET value = ?', (value + 1,))
        work.record_change('counter', str(value), str(value + 1), sys.argv[3])
"""
    env = {**os.environ, "FLEET_STORE": str(path)}
    processes = [subprocess.Popen([sys.executable, "-c", script, str(start), str(writes), f"process:{index}"],
                                  env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for index in range(2)]
    try:
        for process in processes:
            assert process.stdout.readline() == "ready\n"
        start.touch()
        for _ in range(writes):
            for process in processes:
                assert process.stdout.readline() == "next\n", process.stderr.read()
            for process in processes:
                process.stdin.write("write\n")
                process.stdin.flush()
        for process in processes:
            _, stderr = process.communicate(timeout=300)
            assert process.returncode == 0, stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM counter").fetchone()[0] == 2 * writes
    rows = store.history_after(0)
    assert len(rows) == 2 * writes
    assert [row["sequence"] for row in rows] == list(range(1, 2 * writes + 1))
    assert [(row["from"], row["to"]) for row in rows] == [(str(i), str(i + 1)) for i in range(2 * writes)]
    assert all(sum(row["actor"] == f"process:{index}" for row in rows) == writes for index in range(2))


def test_cli_process_change_refreshes_sse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "controller.db"
    monkeypatch.setenv("FLEET_STORE", str(path))
    store = configured_container().store()
    state = FleetState([], container=configured_container(store=store))
    stop = threading.Event()
    watcher = threading.Thread(target=state.follow_history, args=(stop,))
    watcher.start()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/stream", timeout=10) as stream:
            assert stream.readline() == b"event: state\n"
            assert stream.readline().startswith(b"data: ")
            assert stream.readline() == b"\n"
            baseline = state.version
            script = ("from fleet.container import Container\n"
                      "store = Container().store()\n"
                      "with store.unit_of_work() as work:\n"
                      "    work.record_change('work:1', 'ready', 'active', 'cli')\n")
            subprocess.run([sys.executable, "-c", script], env=os.environ.copy(), check=True, timeout=20)
            assert stream.readline() == b"event: state\n"
            assert state.version > baseline
    finally:
        stop.set()
        watcher.join(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
