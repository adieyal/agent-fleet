"""fleet canvas sends the same kernel operations the canvas does."""
import io
import json

import pytest

from fleet.container import configured_container
from fleet_cli.cli import main


@pytest.fixture
def project(monkeypatch):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)
    container = configured_container()
    identity = container.initialized_workspace().edit_registry(lambda registry: registry.create("cli-canvas")).id
    return container, identity


def run(container, *arguments):
    main(["canvas", *arguments], container=container)


def test_init_show_and_a_refused_operation(project, capsys):
    container, identity = project
    run(container, "init", "cli-canvas", "--north-star", "Ship the service")
    assert "space.init: ok" in capsys.readouterr().out
    run(container, "op", "cli-canvas", "item.create", "--args", '{"title": "Write docs"}', "--json")
    item = json.loads(capsys.readouterr().out)["result"]["item"]
    with pytest.raises(SystemExit) as refused:
        run(container, "op", "cli-canvas", "item.move", "--args", json.dumps({"item": item, "stage": "approve"}))
    assert refused.value.code == 3
    assert "refused [stage_skipped] (workflow v1, line 0): New items enter at Plan." in capsys.readouterr().err
    run(container, "show", "cli-canvas")
    shown = capsys.readouterr().out
    assert "Plan → Implement → Approve → Done" in shown and "Write docs" in shown
    run(container, "log", "cli-canvas", "--json")
    assert any(event["tone"] == "refuse" for event in json.loads(capsys.readouterr().out))
    run(container, "ls")
    assert capsys.readouterr().out.strip() == f"{identity}\tcli-canvas"


def test_check_and_compile_read_code_from_stdin(project, capsys, monkeypatch):
    container, _ = project
    run(container, "init", "cli-canvas")
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO('zone "Inbox"\n  agents:\n    place new items here\n    sort by age\n'))
    run(container, "check")
    output = capsys.readouterr().out
    assert "✓     place new items here" in output and "~     sort by age" in output and "1 compiled, 1 guidance" in output
    monkeypatch.setattr("sys.stdin", io.StringIO('zone "Inbox"\n  agents:\n    place new items here\n    sort by age\n'))
    run(container, "compile", "cli-canvas", "zone", "@Inbox", "--base", "1")
    assert "code.compile: ok" in capsys.readouterr().out


def test_agents_and_decision_scope(project, capsys):
    container, _ = project
    run(container, "init", "cli-canvas")
    run(container, "agent", "cli-canvas", "claude", "--host", "worker", "--cwd", "/src/app")
    run(container, "can", "cli-canvas", "destructive", "--json")
    capsys.readouterr()
    run(container, "can", "cli-canvas", "impl", "--json")
    assert json.loads(capsys.readouterr().out)["result"]["level"] == "decide"
    run(container, "show", "cli-canvas", "--json")
    assert json.loads(capsys.readouterr().out)["settings"]["agents"]["claude"] == {
        "mode": "dispatch", "host": "worker", "cwd": "/src/app", "runtime": "claude"}
