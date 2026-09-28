import json
from dataclasses import asdict
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from fleet import transport
from fleet.composition import open_store, open_workspace
from fleet.transport import FleetError
from fleet.modules.workspace import Registry


def test_import_and_backup(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    original = {"hosts": {"home": {}}, "capacity": 10, "projects": {
        "p-00000001": {"name": "One", "links": [{"host": "home", "label": "one"}]},
        "p-00000002": {"name": "Two"}}}
    choices = {"floors": {"p-00000001": 4}, "shuttered": {"p-00000002": {"at": 12, "floor": 2}},
               "focus": {"projects": {"p-00000001": "background"}, "labels": {"scratch": "background"}}}
    config.write_text(json.dumps(original))
    legacy = tmp_path / "workspace.json"
    legacy.write_text(json.dumps(choices))
    workspace = open_workspace()
    assert workspace.registry().project_for("home", "one").id == "p-00000001"
    assert workspace.capacity() == 10
    assert workspace.floors_snapshot() == choices["floors"]
    assert {key: asdict(record) for key, record in workspace.shuttered_snapshot().items()} == choices["shuttered"]
    assert asdict(workspace.focus_snapshot()) == choices["focus"]
    for path in (config, legacy):
        assert path.with_suffix(path.suffix + ".workspace.bak").read_bytes() == path.read_bytes()
    workspace.set_capacity(6)
    assert open_workspace().capacity() == 6
    assert json.loads(config.read_text()) == original
    transport.save_config({**original, "hosts": {"other": {}}})
    assert json.loads(config.read_text()) == {"hosts": {"other": {}}}
    assert workspace.registry().project_for("home", "one").id == "p-00000001"
    assert json.loads(config.with_suffix(".json.workspace.bak").read_text()) == original


def test_units_roll_back_and_stable_floors():
    workspace = open_workspace()
    assert workspace.capacity() == 6
    with pytest.raises(FleetError):
        workspace.set_capacity(11)
    first = workspace.move_in(["home"], "one")
    second = workspace.move_in(["home"], "two")
    before = workspace.snapshot()
    sequence = open_store().latest_sequence()
    with pytest.raises(FleetError):
        workspace.move_in(["home"], "one", shutter=second.project_id)
    with pytest.raises(FleetError):
        workspace.merge(second.project_id, first.project_id)
    assert workspace.snapshot() == before
    assert open_store().latest_sequence() == sequence
    workspace.shutter(first.project_id)
    workspace.restore(first.project_id)
    assert workspace.floors_snapshot() == before.floors
    workspace.set_focus("background", [first.project_id], [])
    assert workspace.floors_snapshot() == before.floors


def test_unchanged_settle_is_read_only():
    workspace = open_workspace()
    workspace.move_in(["home"], "one")
    sequence = open_store().latest_sequence()
    workspace.settle()
    workspace.settle()
    assert open_store().latest_sequence() == sequence


def test_failure_after_partial_move_or_merge_rolls_back(monkeypatch):
    workspace = open_workspace()
    first = workspace.move_in(["home"], "one").project_id
    second = workspace.move_in(["home"], "two").project_id
    before = workspace.snapshot()
    sequence = open_store().latest_sequence()
    original_link = Registry.link
    def fail_link(registry, project_id, host, label):
        original_link(registry, project_id, host, label)
        if label == "new":
            raise RuntimeError("link failed")
    with monkeypatch.context() as patch:
        patch.setattr(Registry, "link", fail_link)
        with pytest.raises(RuntimeError, match="link failed"):
            workspace.move_in(["home"], "new", shutter=first)
    original_merge = Registry.merge
    def fail_merge(registry, keep, other):
        original_merge(registry, keep, other)
        raise RuntimeError("merge failed")
    monkeypatch.setattr(Registry, "merge", fail_merge)
    with pytest.raises(RuntimeError, match="merge failed"):
        workspace.merge(first, second)
    assert workspace.snapshot() == before
    assert open_store().latest_sequence() == sequence


WRITER = """
import os
import sqlite3
import sys
from fleet import cli
from fleet.composition import open_workspace
from fleet.web.server import FleetState
role, project = sys.argv[1:]
state = FleetState([]) if role == 'web' else None
workspace = open_workspace()
cli.open_workspace = lambda: workspace
print('ready', flush=True)
sys.stdin.readline()
if role == 'cli':
    def hold(registry):
        print('held', flush=True)
        sys.stdin.readline()
    workspace.edit_registry(hold)
    print('released', flush=True)
else:
    connection = sqlite3.connect(os.environ['FLEET_STORE'], timeout=0)
    try:
        connection.execute('BEGIN IMMEDIATE')
    except sqlite3.OperationalError as error:
        assert 'locked' in str(error)
        print('protected', flush=True)
    else:
        connection.rollback()
        print('unprotected', flush=True)
    finally:
        connection.close()
for index in range(24):
    sys.stdin.readline()
    url = f'https://example.org/{role}/{index}'
    if role == 'cli':
        cli.main(['project', 'repo', 'add', project, url])
    else:
        state.edit_registry(lambda registry: registry.add_repository(project, url))
    print('written', flush=True)
"""


@pytest.fixture
def contention_store(monkeypatch):
    # SQLite locking is real; volatile storage keeps 48 durable commits fast under disk contention.
    with TemporaryDirectory(prefix="fleet-workspace-", dir="/dev/shm") as directory:
        monkeypatch.setenv("FLEET_STORE", str(Path(directory) / "fleet.db"))
        yield


def test_cli_and_web_processes_preserve_overlapping_edits(contention_store):
    workspace = open_workspace()
    project = workspace.edit_registry(lambda registry: registry.create("Shared"))
    store = open_store()
    sequence = store.latest_sequence()
    processes = [subprocess.Popen([sys.executable, "-c", WRITER, role, project.id],
                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 text=True, env=os.environ.copy()) for role in ("cli", "web")]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        cli_process, web_process = processes
        cli_process.stdin.write("hold\n")
        cli_process.stdin.flush()
        assert cli_process.stdout.readline().strip() == "held"
        web_process.stdin.write("probe\n")
        web_process.stdin.flush()
        assert web_process.stdout.readline().strip() == "protected"
        cli_process.stdin.write("release\n")
        cli_process.stdin.flush()
        assert cli_process.stdout.readline().strip() == "released"
        for _ in range(24):
            for process in processes:
                process.stdin.write("go\n")
                process.stdin.flush()
            for process in processes:
                assert process.stdout.readline().strip() == "written", process.stderr.read()
        for process in processes:
            assert process.wait(timeout=10) == 0, process.stderr.read()
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
    assert set(workspace.registry().get(project.id).repositories) == {
        f"https://example.org/{role}/{index}" for role in ("cli", "web") for index in range(24)}
    history = store.history_after(sequence)
    assert len(history) == 48
    assert [row["actor"] for row in history].count("user") == 24
    assert [row["actor"] for row in history].count("web-user") == 24
    assert [len(json.loads(row["to"])["projects"][project.id]["repositories"]) for row in history] == list(range(1, 49))
