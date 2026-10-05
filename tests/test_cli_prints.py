"""Audit 1 batch 9: the CLI prints what it did, refuses unknown names and names the way out of dead ends."""
from fleet.container import configured_container
from fleet import transport
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from fleet_cli import cli
from fleet import transport


def run(capsys, *arguments: str) -> tuple[str, str]:
    cli.main(list(arguments))
    captured = capsys.readouterr()
    return " ".join(captured.out.split()), " ".join(captured.err.split())


def fails(capsys, *arguments: str) -> str:
    with pytest.raises(SystemExit) as raised:
        cli.main(list(arguments))
    assert raised.value.code == 2
    return " ".join(capsys.readouterr().err.split())


def test_hosts_and_libraries_say_when_none_are_configured(capsys):
    out, _ = run(capsys, "hosts")
    assert "No hosts configured" in out and "fleet host add" in out
    out, _ = run(capsys, "libraries")
    assert "No libraries configured" in out and "fleet library add" in out


def test_host_add_says_what_it_replaced(capsys):
    out, _ = run(capsys, "host", "add", "demo", "--local")
    assert out.startswith("added demo")
    out, _ = run(capsys, "host", "add", "demo", "--ssh", "someone@elsewhere")
    assert "replaced demo (was local, now ssh someone@elsewhere)" in out


def test_host_rm_prints_what_it_removed_and_refuses_unknown_hosts(capsys):
    run(capsys, "host", "add", "demo", "--ssh", "someone@demo")
    run(capsys, "project", "add", "Demo", "--link", "demo:demo")
    out, _ = run(capsys, "host", "rm", "demo")
    assert "removed host demo (ssh someone@demo)" in out
    assert "demo:demo" in out
    assert "no host 'nosuchhost'" in fails(capsys, "host", "rm", "nosuchhost")


def test_library_rm_prints_and_refuses_unknown(capsys, tmp_path):
    project = configured_container().initialized_workspace().edit_registry(lambda registry: registry.create('notes'))
    run(capsys, "library", "add", "notes", str(tmp_path))
    out, _ = run(capsys, "library", "rm", "notes")
    assert f"removed library {project.id} ({tmp_path})" in out and "files" in out
    assert f"no library '{project.id}'" in fails(capsys, "library", "rm", "notes")


def test_project_repo_add_and_rm_print_the_change(capsys, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    out, _ = run(capsys, "project", "repo", "add", project_id, "https://github.com/a/b")
    assert f"added repository https://github.com/a/b to {project_id}" in out
    out, _ = run(capsys, "project", "repo", "rm", project_id, "https://github.com/a/b")
    assert f"removed repository https://github.com/a/b from {project_id}" in out


def test_project_management_prints_that_it_is_permanent_and_wraps_git_errors(capsys, tmp_path, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    error = fails(capsys, "project", "management", project_id, str(tmp_path / "nonrepo"))
    assert "is not a Git working tree root" in error and "fatal:" not in error.split("(")[0]
    repo = tmp_path / "management"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    out, _ = run(capsys, "project", "management", project_id, str(repo))
    assert f"registered {repo} as the management repository of {project_id}" in out
    assert "0 summaries moved" in out and "permanent" in out


def test_management_help_says_permanent(capsys):
    with pytest.raises(SystemExit):
        cli.main(["project", "management", "--help"])
    assert "permanent" in capsys.readouterr().out


def test_unknown_project_names_the_real_list_command(capsys):
    error = fails(capsys, "status", "nosuch")
    assert "fleet project ls" in error and "fleet project list" not in error


def test_send_to_an_unlinked_label_names_the_link_remedy(monkeypatch, capsys):
    monkeypatch.setattr(transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    error = fails(capsys, "send", "-H", "demo", "-p", "newrepo", "-d", "Fix", "-C", "/tmp", "-s", "Fix")
    assert "fleet project ls" in error and "fleet project add NAME --link HOST:LABEL" in error


def test_condition_lists_its_values(capsys, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    item = configured_container().work().add(project=project_id, title='T', goal='g', actor='user')
    error = fails(capsys, "work", "set", item.id, "--condition", "paused", "--actor", "user")
    assert "'on hold'" in error and "'ready for review'" in error


def test_a_new_work_kind_is_announced(capsys, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    out, err = run(capsys, "work", "add", "T", "--project", project_id, "--goal", "g", "--kind", "spike",
                   "--actor", "user")
    assert json.loads(out)["kind"] == "spike"
    assert "new kind 'spike'" in err
    _, err = run(capsys, "work", "add", "U", "--project", project_id, "--goal", "g", "--kind", "spike",
                 "--actor", "user")
    assert err == ""


def test_attention_kind_is_a_choice_and_until_gives_an_example(capsys, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    error = fails(capsys, "attention", "add", "H", "--project", project_id, "--kind", "bogus", "--owner", "user",
                  "--source", "s", "--source-reference", "r", "--context-reference", "c", "--actor", "a")
    assert "decision" in error and "blocker" in error
    item = configured_container().initialized_attention().raise_item(project=project_id, kind='alert', owner='user', source='s', source_reference='r', headline='H', context_reference='c', actor='a')
    error = fails(capsys, "attention", "snooze", item.id, "--until", "tomorrow", "--actor", "a")
    assert "2026-10-02T09:00:00+00:00" in error


def test_a_shuttered_project_names_the_way_out_and_restore_reopens_it(capsys):
    workspace = configured_container().initialized_workspace()
    project = workspace.move_in(["demo"], "demo", name="Demo").project_id
    workspace.shutter(project)
    with pytest.raises(ValueError, match=f"fleet project restore {project}"):
        workspace.require_claims_allowed(project, "demo")
    out, _ = run(capsys, "project", "restore", project)
    assert f"restored {project} Demo to floor" in out
    workspace.require_claims_allowed(project, "demo")


def test_missing_store_names_the_remedy(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FLEET_STORE", str(tmp_path / "missing" / "fleet.db"))
    error = fails(capsys, "status", "x")
    assert "fleet web" in error and "unset FLEET_STORE" in error


def test_guidance_edit_prompts_on_a_terminal(monkeypatch, capsys, project_id, tmp_path):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    terminal = io.StringIO("# Constitution\n\nBody.\n")
    terminal.isatty = lambda: True
    monkeypatch.setattr("sys.stdin", terminal)
    _, err = run(capsys, "guidance", "edit", project_id, "--actor", "user")
    assert "end with Ctrl-D" in err


def test_send_hold_help_says_how_to_start(capsys):
    with pytest.raises(SystemExit):
        cli.main(["send", "--help"])
    assert "fleet start" in " ".join(capsys.readouterr().out.split())


def test_start_starts_a_held_run(monkeypatch, capsys, project_id, override_cli_method, cli_container):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    calls = []

    def call(host, arguments, **kwargs):
        calls.append(arguments)
        run = configured_container().execution().runs()[-1]
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 4,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1],
                "start_requested": arguments[0] == "start", "status": "queued", "steps": [{}],
                "description": "Task", "permission": "acceptEdits"}

    monkeypatch.setattr(transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    override_cli_method('references', 'job', lambda reference: (SimpleNamespace(name="fake"), reference.split(":")[1]))
    monkeypatch.setattr(transport, "call", call)
    cli.main(["send", "-p", "p", "-d", "Task", "-s", "Ship", "-H", "fake", "-C", "/repo", "--hold", "--json"], container=cli_container)
    job = json.loads(capsys.readouterr().out)["job"]
    out, _ = run(capsys, "start", job)
    start = calls[-1]
    assert start[0] == "start" and start[start.index("--fingerprint") + 1] == calls[0][calls[0].index("--fingerprint") + 1]
    assert f"started {job}" in out


def test_resend_prints_text_without_json(monkeypatch, capsys, project_id):
    configured_container().initialized_workspace().edit_registry(lambda registry: registry.link(project_id, 'fake', 'worker-p'))
    def call(host, arguments, **kwargs):
        run = configured_container().execution().runs()[-1]
        return {"id": run.remote_job_id, "run_id": run.id, "schema_version": 4,
                "fingerprint": arguments[arguments.index("--fingerprint") + 1] if "--fingerprint" in arguments
                else None, "start_requested": False, "status": "queued", "steps": [{}], "description": "Task"}

    monkeypatch.setattr(transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(transport, "call", call)
    arguments = ["send", "-p", "p", "-d", "Task", "-s", "Ship", "-H", "fake", "-C", "/repo", "--hold", "--id", "x"]
    run(capsys, *arguments)
    out, _ = run(capsys, *arguments)
    assert "already sent" in out and not out.startswith("{")
