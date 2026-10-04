"""Audit 1 batch 8: CLI help says what a command changes, send says what it started, actors are passed through."""
import argparse
import json
from types import SimpleNamespace

import pytest

from fleet import cli, composition


def help_text(capsys, *arguments: str) -> str:
    with pytest.raises(SystemExit):
        cli.main([*arguments, "--help"])
    return " ".join(capsys.readouterr().out.split())


def fake_worker(monkeypatch, calls: list) -> None:
    def call(host, arguments, **kwargs):
        calls.append(arguments)
        run = composition.open_execution().runs()[-1]
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 4,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1] if "--fingerprint" in arguments else None,
                "start_requested": False, "status": "queued", "steps": [{}], "description": "Task",
                "permission": "acceptEdits"}

    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(cli.transport, "call", call)


@pytest.mark.parametrize("command", [("send",), ("dispatch",), ("run", "retry")])
def test_commands_that_start_an_agent_say_so_and_name_the_default_permission(capsys, command):
    text = help_text(capsys, *command).lower()
    assert "starts a" in text and "agent" in text
    assert "acceptedits" in text and "workspace-write" in text


def test_add_and_answer_help_state_both_effects(capsys):
    add = help_text(capsys, "add")
    assert "blocked" in add and "answer" in add
    answer = help_text(capsys, "answer")
    assert "fleet attention list" in answer
    assert "blocked" in answer and "decision" in answer


def test_top_level_help_groups_commands_with_a_quick_start(capsys):
    text = help_text(capsys)
    for group in ("Jobs", "Work", "Projects", "Hosts", "Agent-internal"):
        assert f"{group}:" in text
    assert text.index("fleet host add") < text.index("fleet project add") < text.index("fleet send")
    assert "kitchen" not in text


def subcommands(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next((action for action in parser._actions if isinstance(action, argparse._SubParsersAction)), None)


def test_every_command_is_in_one_group_and_every_subcommand_has_help(capsys):
    commands = subcommands(cli.build_parser())
    grouped = [name for names in cli.COMMAND_GROUPS.values() for name in names]
    assert sorted(grouped) == sorted(commands.choices)
    text = help_text(capsys)
    assert all(f"{name} " in text for name in grouped)
    missing = []
    for name, parser in commands.choices.items():
        nested = subcommands(parser)
        if nested is not None:
            helped = {choice.dest for choice in nested._choices_actions if choice.help}
            missing += [f"{name} {sub}" for sub in nested.choices if sub not in helped]
            for sub, deeper in nested.choices.items():
                if subcommands(deeper) is not None:
                    deep = {choice.dest for choice in subcommands(deeper)._choices_actions if choice.help}
                    missing += [f"{name} {sub} {leaf}" for leaf in subcommands(deeper).choices if leaf not in deep]
    assert missing == []


def test_send_prints_the_run_permission_and_guidance(monkeypatch, capsys, project_id):
    composition.open_workspace().edit_registry(lambda registry: registry.link(project_id, "fake", "worker-p"))
    calls = []
    fake_worker(monkeypatch, calls)
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold"])
    output = capsys.readouterr().out
    run, = composition.open_execution().runs()
    assert f"run {run.id[:8]}" in output
    assert "permission acceptEdits" in output
    assert "no guidance" in output


def test_runtime_and_agent_are_aliases_on_send_and_dispatch(monkeypatch, project_id):
    composition.open_workspace().edit_registry(lambda registry: registry.link(project_id, "fake", "worker-p"))
    calls = []
    fake_worker(monkeypatch, calls)
    item = composition.open_work().add(project=project_id, title="Task", goal="Ship", actor="user")
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold", "--json", "--runtime", "codex"])
    cli.main(["dispatch", item.id, "Ship", "--host", "fake", "--cwd", "/repo", "--json", "--agent", "codex"])
    creates = [call for call in calls if call[0] == "create"]
    assert [call[call.index("--agent") + 1] for call in creates] == ["codex", "codex"]


def test_send_records_the_given_actor(monkeypatch, project_id):
    composition.open_workspace().edit_registry(lambda registry: registry.link(project_id, "fake", "worker-p"))
    fake_worker(monkeypatch, [])
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold", "--json", "--actor", "orchestrator"])
    action, = composition.open_execution().actions()
    assert action.actor == "orchestrator"


def test_send_inside_a_fleet_job_records_the_job_as_actor(monkeypatch, project_id):
    composition.open_workspace().edit_registry(lambda registry: registry.link(project_id, "fake", "worker-p"))
    fake_worker(monkeypatch, [])
    monkeypatch.setenv("FLEET_JOB_ID", "27563ec6-a70f")
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold", "--json"])
    action, = composition.open_execution().actions()
    assert action.actor == "job:27563ec6-a70f"


def test_run_retry_and_resolve_unknown_pass_the_actor(monkeypatch, project_id):
    composition.open_workspace().edit_registry(lambda registry: registry.link(project_id, "fake", "worker-p"))
    seen = {}

    class Execution:
        def retry(self, run, *, actor, idempotency_key):
            seen["retry"] = actor
            return SimpleNamespace(created=False, run=SimpleNamespace(id=run))

        def resolve_unknown(self, run, *, actor):
            seen["resolve"] = actor
            return SimpleNamespace(id=run)

    monkeypatch.setattr(cli, "open_execution", Execution)
    original_dispatch = composition.open_dispatch
    def dispatch():
        service = original_dispatch()
        service.services.execution = Execution()
        return service
    monkeypatch.setattr(cli, "open_dispatch", dispatch)
    monkeypatch.setattr(cli, "asdict", lambda value: vars(value))
    cli.main(["run", "retry", "r1", "--actor", "orchestrator"])
    cli.main(["run", "resolve-unknown", "r1", "--actor", "orchestrator"])
    assert seen == {"retry": "orchestrator", "resolve": "orchestrator"}


def test_answer_records_the_given_actor(capsys, project_id):
    composition.open_workspace().edit_registry(lambda registry: registry.link(project_id, "fake", "worker-p"))
    work = composition.open_work()
    item = work.add(project=project_id, title="Delivery", goal="Ship", actor="author")
    question = composition.open_attention().raise_item(project=project_id, work_item=item.id, kind="decision",
        owner="user", source="manual", source_reference="q", headline="Choose", context_reference="doc:1",
        actor="author")
    cli.main(["answer", question.id, "Direct", "--actor", "orchestrator"])
    assert json.loads(capsys.readouterr().out)["actor"] == "orchestrator"
