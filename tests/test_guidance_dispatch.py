"""A job dispatched on work gets the project constitution and nearest epic charter, pinned to the versions it saw."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet import cli, composition
from fleet.orchestration import ControllerCommands
from fleet.transport import Host

FIXTURES = Path(__file__).parent / "fixtures" / "guidance"
CONSTITUTION = (FIXTURES / "invoice-training.constitution.md").read_text()
CHARTER = (FIXTURES / "epic-positional-transcriber.charter.md").read_text()


@pytest.fixture
def worker(monkeypatch):
    """A fake fleetd: records create/start calls with their steps, and the context each push carried."""
    calls, pushes = [], []

    def call(host, arguments, stdin_text=None, **kwargs):
        run = composition.open_execution().runs()[-1]
        calls.append((arguments, stdin_text))
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 4,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1],
                "start_requested": arguments[0] == "start", "status": "queued", "steps": [{}],
                "description": "Task"}

    def push(host, job, paths):
        pushes.append({Path(path).name: Path(path).read_text() for path in paths})

    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(cli.transport, "call", call)
    monkeypatch.setattr(cli, "push_context", push)
    return SimpleNamespace(calls=calls, pushes=pushes,
                           prompts=lambda: [json.loads(stdin)[0]["prompt"] for arguments, stdin in calls
                                            if arguments[0] == "create"])


@pytest.fixture
def tree(project_id):
    for host in ("fake", "controller"):
        composition.open_workspace().edit_registry(lambda registry: registry.link(project_id, host, "worker-p"))
    work = composition.open_work()
    epic = work.add(project=project_id, title="Transcriber", goal="Read", kind="epic", actor="user")
    milestone = work.add(project=project_id, title="M1", goal="Read", kind="milestone", parent=epic.id, actor="user")
    task = work.add(project=project_id, title="Task", goal="Ship", parent=milestone.id, actor="user")
    return SimpleNamespace(project=project_id, epic=epic, task=task)


def register(tmp_path, project):
    repo = tmp_path / "management"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True, timeout=10)
    records = composition.open_records()
    records.register(project, repo, actor="user")
    return records


def dispatch(item: str, *extra: str) -> None:
    cli.main(["dispatch", item, "Ship it", "--runtime", "codex", "--host", "fake", "--cwd", "/repo", "--json", *extra])


def action_guidance():
    return composition.open_execution().actions()[-1].guidance


def test_dispatch_attaches_constitution_and_nearest_charter(tmp_path, tree, worker, capsys):
    records = register(tmp_path, tree.project)
    constitution = records.write_guidance(tree.project, CONSTITUTION, actor="user")
    charter = records.write_guidance(tree.project, CHARTER, epic=tree.epic.id, actor="user")
    dispatch(tree.task.id)
    assert worker.pushes == [{"CONSTITUTION.md": CONSTITUTION, "CHARTER.md": CHARTER}]
    prompt, = worker.prompts()
    assert prompt.startswith("Guidance: your context directory has CONSTITUTION.md (the project's constitution) "
                             "and CHARTER.md (your epic's charter). This brief overrides the charter")
    assert "overrides the constitution" in prompt and f"fleet decision record --work-item {tree.task.id}" in prompt
    assert prompt.endswith("\n\nShip it")
    assert action_guidance() == dict(
        project=tree.project, epic=tree.epic.id,
        constitution=dict(path="constitution.md", revision=constitution.version.revision, version=1),
        charter=dict(path=f"charters/{tree.epic.id}.md", revision=charter.version.revision, version=1))
    capsys.readouterr()
    cli.main(["status", tree.project])
    assert "Guidance: constitution version 1, charter version 1" in capsys.readouterr().out


def test_retry_delivers_the_pinned_versions(tmp_path, tree, worker):
    records = register(tmp_path, tree.project)
    records.write_guidance(tree.project, CONSTITUTION, actor="user")
    dispatch(tree.task.id)
    records.write_guidance(tree.project, CONSTITUTION + "\nRevised.\n", actor="user")
    run, = composition.open_execution().runs()
    cli.main(["run", "resolve-unknown", run.id])
    cli.main(["run", "retry", run.id])
    assert worker.pushes == [{"CONSTITUTION.md": CONSTITUTION}] * 2
    assert worker.prompts()[0] == worker.prompts()[1]


def test_constitution_only_when_no_epic_has_a_charter(tmp_path, tree, worker):
    records = register(tmp_path, tree.project)
    records.write_guidance(tree.project, CONSTITUTION, actor="user")
    dispatch(tree.task.id)
    assert worker.pushes == [{"CONSTITUTION.md": CONSTITUTION}]
    prompt, = worker.prompts()
    assert prompt.startswith("Guidance: your context directory has CONSTITUTION.md (the project's constitution). ")
    assert action_guidance()["charter"] is None and action_guidance()["epic"] == tree.epic.id


@pytest.mark.parametrize("registered", [False, True])
def test_unguided_project_dispatches_as_before(tmp_path, tree, worker, capsys, registered):
    if registered:
        register(tmp_path, tree.project)
    dispatch(tree.task.id)
    assert worker.pushes == [] and worker.prompts() == ["Ship it"] and action_guidance() is None
    capsys.readouterr()
    cli.main(["status", tree.project])
    assert "Guidance: none attached" in capsys.readouterr().out


def test_send_with_context_adds_guidance_and_refuses_a_name_clash(tmp_path, tree, worker, capsys):
    records = register(tmp_path, tree.project)
    records.write_guidance(tree.project, CHARTER, epic=tree.epic.id, actor="user")
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    send = ["send", "--project", "p", "--description", "Task", "--step", "Ship", "--work-item", tree.task.id,
            "--host", "fake", "--cwd", "/repo", "--json"]
    cli.main([*send, "-c", str(notes)])
    assert worker.pushes == [{"notes.md": "notes", "CHARTER.md": CHARTER}]
    clash = tmp_path / "CHARTER.md"
    clash.write_text("mine")
    with pytest.raises(SystemExit):
        cli.main([*send, "-c", str(clash)])
    assert "context already has CHARTER.md" in capsys.readouterr().err


def test_orchestrate_and_its_dispatches_are_guided(tmp_path, tree, worker, capsys, monkeypatch):
    records = register(tmp_path, tree.project)
    records.write_guidance(tree.project, CONSTITUTION, actor="user")
    records.write_mandate(tree.project, "mandate.json", json.dumps(dict(
        goal="Ship", constraints=[], escalation_conditions=[], criteria_it_may_judge=[],
        decision_authority=["dispatch"])), key="mandate", actor="user")
    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: Host(name, None))
    cli.main(["orchestrate", tree.task.id, "--mandate", "mandate.json", "--host", "controller",
              "--runtime", "codex", "--cwd", str(tmp_path)])
    activation = json.loads(capsys.readouterr().out)["activation"]
    prompt, = worker.prompts()
    assert prompt.startswith("Guidance: your context directory has CONSTITUTION.md") and f"orchestrator for work item {tree.task.id}" in prompt
    assert worker.pushes == [{"CONSTITUTION.md": CONSTITUTION}]
    result = ControllerCommands(composition.open_store(), activation).execute("dispatch", dict(
        host="worker", runtime="codex", reason="Go", idempotency_key="child",
        payload=dict(cwd="/repo", arguments=["create"], steps=[dict(prompt="Child")], context=None, hold=False)))
    action = composition.open_execution().get_action(result.run.action)
    assert action.guidance["constitution"]["version"] == 1
    assert action.payload["steps"][0]["prompt"].startswith("Guidance: ")
