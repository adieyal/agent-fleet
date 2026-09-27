import json
from types import SimpleNamespace

import pytest

from fleet import cli, composition


@pytest.mark.parametrize("legacy", [False, True])
def test_cli_dispatch_commits_before_transport_and_send_wraps_it(monkeypatch, capsys, legacy):
    item = composition.open_work().add(project="p", title="Task", goal="Ship", actor="user")
    calls = []

    def call(host, arguments, **kwargs):
        run = composition.open_execution().runs()[-1]
        assert composition.open_execution().claims()[-1].run == run.id
        calls.append(arguments)
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 3,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1],
                "start_requested": any(call[0] == "start" for call in calls),
                "status": "queued", "steps": [{}], "description": "Task"}

    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(cli.transport, "call", call)
    arguments = (["send", "--project", "p", "--description", "Task", "--step", "Ship", "--work-item", item.id]
                 if legacy else ["dispatch", item.id, "Ship", "--runtime", "codex"])
    arguments += ["--host", "fake", "--cwd", "/repo", "--json", "--id", "request"]
    cli.main(arguments)
    first = json.loads(capsys.readouterr().out)
    cli.main(arguments)
    second = json.loads(capsys.readouterr().out)
    assert first["job"] == second["job"]
    assert [call[0] for call in calls] == ["create", "start", "reconcile"]
    run = composition.open_execution().runs()[0]
    with pytest.raises(SystemExit):
        cli.main(["run", "retry", run.id])
    cli.main(["run", "resolve-unknown", run.id])
    assert composition.open_execution().runs()[0].reason == "resolved unknown"
    cli.main(["run", "retry", run.id])
    runs = composition.open_execution().runs()
    assert len(runs) == 2 and runs[0].action == runs[1].action
    assert runs[0].remote_job_id != runs[1].remote_job_id


def test_dispatch_requires_explicit_cwd():
    with pytest.raises(SystemExit):
        cli.main(["dispatch", "work", "Ship", "--host", "fake", "--runtime", "codex"])


@pytest.mark.parametrize("refused", ["create", "start"])
def test_dispatch_preserves_refusal_when_reconcile_fails(monkeypatch, capsys, refused):
    calls = []

    def call(host, arguments, **kwargs):
        calls.append(arguments[0])
        if arguments[0] == refused:
            raise cli.FleetError("fake: working directory does not exist")
        if arguments[0] == "reconcile":
            raise cli.FleetError("no such run")
        run, = composition.open_execution().runs()
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 3,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1],
                "start_requested": False, "status": "queued"}

    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(cli.transport, "call", call)
    with pytest.raises(SystemExit):
        cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship",
                  "--host", "fake", "--cwd", "/repo", "--id", "request"])
    captured = capsys.readouterr()
    assert "working directory does not exist" in captured.out + captured.err
    assert calls == (["create", "reconcile"] if refused == "create" else ["create", "start", "reconcile"])
    execution = composition.open_execution()
    assert execution.runs()[0].status == "unknown outcome"
    assert execution.claims()[0].active


def test_send_without_work_keeps_unknown_intent_after_lost_create_reply(monkeypatch):
    calls = []

    def call(host, arguments, **kwargs):
        calls.append(arguments)
        raise cli.FleetError("reply lost")

    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(cli.transport, "call", call)
    arguments = ["send", "--project", "legacy", "--description", "Task", "--step", "Ship",
                 "--host", "fake", "--cwd", "/repo", "--id", "request"]
    with pytest.raises(SystemExit):
        cli.main(arguments)
    with pytest.raises(SystemExit):
        cli.main(arguments)
    assert [call[0] for call in calls] == ["create", "reconcile", "reconcile"]
    execution = composition.open_execution()
    assert execution.runs()[0].status == "unknown outcome"
    assert execution.claims()[0].active
    assert execution.actions()[0].work_item is None


@pytest.mark.parametrize("dropped", ["create", "start"])
def test_dropped_dispatch_reply_reconciles_by_run_id(monkeypatch, dropped):
    calls = []
    def call(host, arguments, **kwargs):
        run, = composition.open_execution().runs()
        calls.append(arguments)
        if arguments[0] == dropped:
            raise cli.FleetError("reply lost")
        if arguments[0] == "reconcile":
            assert arguments[1] == run.id
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 3,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1],
                "start_requested": dropped == "start" and arguments[0] == "reconcile",
                "status": "running" if arguments[0] == "reconcile" and dropped == "start" else "queued", "steps": [{}],
                "description": "Task"}
    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(cli.transport, "call", call)
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship",
              "--host", "fake", "--cwd", "/repo", "--id", "request", "--json"])
    assert [call[0] for call in calls] == (["create", "reconcile", "start"] if dropped == "create"
                                        else ["create", "start", "reconcile"])
