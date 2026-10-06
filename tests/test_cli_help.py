"""Audit 1 batch 8: CLI help says what a command changes, send says what it started, actors are passed through."""
from fleet.container import configured_container
from fleet import transport
import argparse
import json
from types import SimpleNamespace

import pytest

from fleet_cli import cli


def help_text(capsys, *arguments: str) -> str:
    with pytest.raises(SystemExit):
        cli.main([*arguments, "--help"])
    return " ".join(capsys.readouterr().out.split())


def fake_worker(monkeypatch, calls: list) -> None:
    def call(host, arguments, **kwargs):
        calls.append(arguments)
        run = configured_container().execution().runs()[-1]
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 4,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1] if "--fingerprint" in arguments else None,
                "start_requested": False, "status": "queued", "steps": [{}], "description": "Task",
                "permission": "acceptEdits"}

    monkeypatch.setattr(transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(transport, "call", call)


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
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    calls = []
    fake_worker(monkeypatch, calls)
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold"])
    output = capsys.readouterr().out
    run, = configured_container().execution().runs()
    assert f"run {run.id[:8]}" in output
    assert "permission acceptEdits" in output
    assert "no guidance" in output


def test_runtime_and_agent_are_aliases_on_send_and_dispatch(monkeypatch, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    calls = []
    fake_worker(monkeypatch, calls)
    item = configured_container().work().add(project=project_id, title='Task', goal='Ship', actor='user')
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold", "--json", "--runtime", "codex"])
    cli.main(["dispatch", item.id, "Ship", "--host", "fake", "--cwd", "/repo", "--json", "--agent", "codex"])
    creates = [call for call in calls if call[0] == "create"]
    assert [call[call.index("--agent") + 1] for call in creates] == ["codex", "codex"]


def test_send_records_the_given_actor(monkeypatch, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    fake_worker(monkeypatch, [])
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold", "--json", "--actor", "orchestrator"])
    action, = configured_container().execution().actions()
    assert action.actor == "orchestrator"


def test_send_inside_a_fleet_job_records_the_job_as_actor(monkeypatch, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    fake_worker(monkeypatch, [])
    item = configured_container().work().add(project=project_id, title='Parent', goal='Dispatch', actor='user')
    parent_run = configured_container().execution().dispatch(item.id, host='fake', runtime='codex', actor='user', reason='Go',
        idempotency_key='parent', remote_job_id='27563ec6-a70f',
        payload=dict(cwd='/repo', arguments=[], steps=[dict(prompt='Go')], context=None, hold=False)).run
    monkeypatch.setenv("FLEET_JOB_ID", "27563ec6-a70f")
    cli.main(["send", "--project", "p", "--description", "Task", "--step", "Ship", "--host", "fake",
              "--cwd", "/repo", "--hold", "--json"])
    action, = [action for action in configured_container().execution().actions() if action.id != parent_run.action]
    assert action.actor == "job:27563ec6-a70f"


def test_run_retry_and_resolve_unknown_pass_the_actor(monkeypatch, project_id, cli_container):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    seen = {}

    class Execution:
        def retry(self, run, *, actor, idempotency_key):
            seen["retry"] = actor
            return SimpleNamespace(created=False, run=SimpleNamespace(id=run))

        def resolve_unknown(self, run, *, actor):
            seen["resolve"] = actor
            return SimpleNamespace(id=run)

    cli_container.execution.override(Execution())
    monkeypatch.setattr(cli, "asdict", lambda value: vars(value))
    cli.main(["run", "retry", "r1", "--actor", "orchestrator"], container=cli_container)
    cli.main(["run", "resolve-unknown", "r1", "--actor", "orchestrator"], container=cli_container)
    assert seen == {"retry": "orchestrator", "resolve": "orchestrator"}


def test_answer_records_the_given_actor(capsys, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    work = configured_container().work()
    item = work.add(project=project_id, title="Delivery", goal="Ship", actor="author")
    question = configured_container().initialized_attention().raise_item(project=project_id, work_item=item.id, kind='decision', owner='user', source='manual', source_reference='q', headline='Choose', context_reference='doc:1', actor='author')
    cli.main(["answer", question.id, "Direct", "--actor", "orchestrator"])
    assert json.loads(capsys.readouterr().out)["actor"] == "orchestrator"
