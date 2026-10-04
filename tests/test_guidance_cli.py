"""Constitutions and charters: versioned in the management repository through records, read and written by the CLI."""

import json
import subprocess
from pathlib import Path

import pytest

from fleet.container import configured_container
from fleet import cli

FIXTURES = Path(__file__).parent / "fixtures" / "guidance"
CONSTITUTION = (FIXTURES / "invoice-training.constitution.md").read_text()
CHARTER = (FIXTURES / "epic-positional-transcriber.charter.md").read_text()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, timeout=10).stdout.strip()


@pytest.fixture
def registered(tmp_path, project_id):
    repo = tmp_path / "management"
    repo.mkdir()
    git(repo, "init")
    cli.main(["project", "management", project_id, str(repo)])
    return project_id, repo


@pytest.fixture
def epic(project_id):
    return configured_container().work().add(project=project_id, title='Positional transcriber', goal='Read by position', kind='epic', actor='user')


def edit(subject: str, body: str, tmp_path: Path, *extra: str) -> None:
    source = tmp_path / "body.md"
    source.write_text(body)
    cli.main(["guidance", "edit", subject, "--file", str(source), *extra])


def show(subject: str, capsys, *extra: str) -> dict:
    capsys.readouterr()
    cli.main(["guidance", "show", subject, "--json", *extra])
    return json.loads(capsys.readouterr().out)


def test_constitution_edit_commits_with_actor_and_run(registered, tmp_path, capsys):
    project, repo = registered
    edit("p", CONSTITUTION, tmp_path, "--actor", "claude", "--run", "run-1")
    assert "recorded constitution.md version 1 by claude" in capsys.readouterr().out
    assert (repo / "constitution.md").read_text() == CONSTITUTION
    message = git(repo, "log", "-1", "--format=%an%n%B")
    assert message.startswith("claude\n") and "Source-Run: run-1" in message
    guidance = show(project, capsys)
    assert guidance["body"] == CONSTITUTION
    assert guidance["version"] | {"time": None, "revision": None} == dict(
        number=1, actor="claude", source_run="run-1", time=None, revision=None)
    assert guidance["version"]["revision"] == git(repo, "rev-parse", "HEAD")


def test_constitution_from_stdin_and_history_newest_first(registered, tmp_path, capsys, monkeypatch):
    project, _ = registered
    edit(project, CONSTITUTION, tmp_path, "--actor", "user")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(CONSTITUTION + "\n## Upkeep\nMore.\n"))
    cli.main(["guidance", "edit", project, "--actor", "web-user"])
    capsys.readouterr()
    cli.main(["guidance", "history", project, "--json"])
    versions = json.loads(capsys.readouterr().out)
    assert [(v["number"], v["actor"], v["source_run"]) for v in versions] == [(2, "web-user", None), (1, "user", None)]
    assert show(project, capsys, "--version", "1")["body"] == CONSTITUTION
    assert show(project, capsys)["body"].endswith("More.\n")
    cli.main(["guidance", "history", project])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("version 2 by web-user at ") and lines[1].startswith("version 1 by user at ")


def test_charter_shows_the_constitution_version_it_inherits(registered, epic, tmp_path, capsys):
    project, repo = registered
    edit(epic.id, CHARTER, tmp_path, "--actor", "user")
    charter = show(epic.id, capsys)
    assert charter["path"] == f"charters/{epic.id}.md" and (repo / charter["path"]).read_text() == CHARTER
    assert charter["inherits"] is None and charter["constitution"] is None
    cli.main(["guidance", "show", epic.id])
    assert f"Inherits no constitution: none recorded for {project}" in capsys.readouterr().out

    edit(project, CONSTITUTION, tmp_path, "--actor", "user")
    charter = show(epic.id, capsys)
    assert charter["inherits"] is None and charter["constitution"]["number"] == 1
    edit(epic.id, CHARTER + "\nRevised.\n", tmp_path, "--actor", "claude")
    edit(project, CONSTITUTION + "\nRevised.\n", tmp_path, "--actor", "user")
    charter = show(epic.id, capsys)
    assert (charter["version"]["number"], charter["inherits"]["number"], charter["constitution"]["number"]) == (2, 1, 2)
    cli.main(["guidance", "show", epic.id])
    output = capsys.readouterr().out
    assert f"Charter of epic {epic.id}, version 2 by claude" in output
    assert "Inherits constitution version 1; current constitution version 2" in output
    assert output.endswith(CHARTER + "\nRevised.\n")


def test_a_project_without_a_repository_gets_one_on_its_first_edit(project_id, tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["guidance", "show", project_id])
    assert f"no constitution of {project_id} recorded" in capsys.readouterr().err
    edit(project_id, CONSTITUTION, tmp_path, "--actor", "user")
    assert "version 1" in capsys.readouterr().out
    cli.main(["guidance", "show", project_id])
    assert CONSTITUTION.strip().splitlines()[0] in capsys.readouterr().out


@pytest.mark.parametrize("body, message", [("  \n", "guidance is empty"), (CONSTITUTION, "unchanged from version 1")])
def test_empty_or_unchanged_edits_are_refused(registered, tmp_path, capsys, body, message):
    project, repo = registered
    edit(project, CONSTITUTION, tmp_path, "--actor", "user")
    with pytest.raises(SystemExit):
        edit(project, body, tmp_path, "--actor", "user")
    assert message in capsys.readouterr().err
    assert git(repo, "rev-list", "--count", "HEAD") == "1"


def test_missing_guidance_and_non_epics_are_reported(registered, project_id, tmp_path, capsys):
    task = configured_container().work().add(project=project_id, title='Task', goal='Do', actor='user')
    with pytest.raises(SystemExit):
        cli.main(["guidance", "show", project_id])
    assert "no constitution of" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        edit(task.id, CHARTER, tmp_path, "--actor", "user")
    assert "is a task; charters belong to epics" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["guidance", "show", project_id, "--version", "3"])
    assert "has no version 3" in capsys.readouterr().err
    cli.main(["guidance", "history", project_id])
    assert capsys.readouterr().out == "No versions recorded.\n"


def test_promote_adds_a_decision_to_the_charter(registered, epic, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)
    project, repo = registered
    decision = configured_container().decisions().record_guided(epic.id, actor='codex', question='Fees as freight?', answer='No', principle='Charter: decision 3')
    with pytest.raises(SystemExit):
        cli.main(["guidance", "promote", decision.id, "--epic", epic.id, "--actor", "user"])
    assert "no charter recorded" in capsys.readouterr().err
    edit(epic.id, CHARTER, tmp_path, "--actor", "user")
    cli.main(["guidance", "promote", decision.id, "--epic", epic.id, "--actor", "user"])
    assert "version 2 by user" in capsys.readouterr().out
    assert f"8. {decision.time.date()}: Fees as freight? — No (principle: Charter: decision 3; decision " \
           f"{decision.id[:8]} by codex)\n" in (repo / f"charters/{epic.id}.md").read_text()
