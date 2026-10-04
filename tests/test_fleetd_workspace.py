"""fleetd reports each job's workspace: the git checkout its cwd is in, read from git and never invented."""
from fleet import transport
import json
import subprocess
import time
from io import StringIO
from types import SimpleNamespace

import pytest

from fleet.remote import fleetd


def run_git(cwd, *arguments):
    return subprocess.run(["git", "-C", str(cwd), *arguments], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    run_git(main, "init", "-q", "-b", "trunk")
    run_git(main, "config", "user.email", "t@example.com")
    run_git(main, "config", "user.name", "T")
    (main / "a.txt").write_text("a\n")
    run_git(main, "add", "a.txt")
    run_git(main, "commit", "-q", "-m", "first")
    return main


def test_a_main_checkout_reports_its_branch_head_and_dirty_paths(repository):
    (repository / "a.txt").write_text("changed\n")
    (repository / "new.txt").write_text("untracked\n")
    (repository / "sub").mkdir()
    workspace, reason = fleetd.collect_workspace(str(repository / "sub"))
    assert reason is None
    assert {key: workspace[key] for key in ("toplevel", "linked_worktree", "repository", "branch", "detached",
                                             "head", "dirty")} == {
        "toplevel": str(repository), "linked_worktree": False, "repository": str(repository), "branch": "trunk",
        "detached": False, "head": run_git(repository, "rev-parse", "--short", "HEAD"), "dirty": 2}
    assert isinstance(workspace["collected_at"], float)


def test_a_linked_worktree_names_the_main_repository_it_belongs_to(repository, tmp_path):
    linked = tmp_path / "linked"
    run_git(repository, "worktree", "add", "-q", "-b", "feat/x", str(linked))
    workspace, reason = fleetd.collect_workspace(str(linked))
    assert reason is None
    assert (workspace["toplevel"], workspace["linked_worktree"], workspace["repository"], workspace["branch"],
            workspace["dirty"]) == (str(linked), True, str(repository), "feat/x", 0)


def test_a_detached_head_has_no_branch(repository):
    run_git(repository, "checkout", "-q", "--detach")
    workspace, reason = fleetd.collect_workspace(str(repository))
    assert reason is None
    assert (workspace["branch"], workspace["detached"]) == (None, True)
    assert workspace["head"] == run_git(repository, "rev-parse", "--short", "HEAD")


def test_a_branch_without_commits_has_no_head(tmp_path):
    run_git(tmp_path, "init", "-q", "-b", "empty")
    workspace, reason = fleetd.collect_workspace(str(tmp_path))
    assert reason is None
    assert (workspace["branch"], workspace["head"]) == ("empty", None)


def test_outside_git_the_workspace_is_null_with_its_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert fleetd.collect_workspace(str(tmp_path)) == (None, "not a git repository")
    assert fleetd.collect_workspace(str(tmp_path / "gone")) == (None, "working directory is missing")


def test_without_git_the_reason_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert fleetd.collect_workspace(str(tmp_path)) == (None, "git is not installed")


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(fleetd.signal, "signal", lambda *args: None)
    return tmp_path / "jobs"


def write_job(directory, cwd, **fields):
    job = {"id": "job", "agent": "claude", "project": "p", "description": "D", "cwd": str(cwd),
           "permission": "default", "created_at": 1, "runner_pid": None,
           "steps": [fleetd.make_step(0, "only", None)], **fields}
    (directory / "job").mkdir(parents=True, exist_ok=True)
    (directory / "job" / "job.json").write_text(json.dumps(job))
    return job


def test_the_summary_says_a_job_from_before_workspaces_was_not_collected(jobs, tmp_path):
    summary = fleetd.job_summary(write_job(jobs, tmp_path), 0)
    assert (summary["workspace"], summary["workspace_reason"]) == (None, "not collected yet")


def test_the_runner_records_the_workspace_as_a_step_starts_and_ends(jobs, repository, monkeypatch):
    write_job(jobs, repository)
    seen = []
    real = fleetd.collect_workspace

    def collect(cwd):
        seen.append(cwd)
        return real(cwd)

    popen = subprocess.Popen

    def agent(command, **kwargs):
        if command[0] == "git":
            return popen(command, **kwargs)
        (repository / "made.txt").write_text("by the agent\n")
        result = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "FLEET_STATUS: done",
                             "session_id": "s"}) + "\n"
        return SimpleNamespace(pid=123, stdout=StringIO(result), wait=lambda: 0)

    monkeypatch.setattr(fleetd, "collect_workspace", collect)
    monkeypatch.setattr(fleetd.subprocess, "Popen", agent)
    fleetd.run_job("job")
    summary = fleetd.job_summary(fleetd.read_job("job"), 0)
    assert seen == [str(repository), str(repository)]
    assert summary["workspace_reason"] is None
    assert (summary["workspace"]["branch"], summary["workspace"]["dirty"]) == ("trunk", 1)


def test_a_collection_fault_is_reported_and_does_not_cost_the_step(jobs, tmp_path, monkeypatch):
    write_job(jobs, tmp_path)

    def broken(cwd):
        raise RuntimeError("boom")

    result = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "FLEET_STATUS: done",
                         "session_id": "s"}) + "\n"
    monkeypatch.setattr(fleetd, "collect_workspace", broken)
    monkeypatch.setattr(fleetd.subprocess, "Popen",
                        lambda command, **kwargs: SimpleNamespace(pid=1, stdout=StringIO(result), wait=lambda: 0))
    fleetd.run_job("job")
    job = fleetd.read_job("job")
    assert job["steps"][0]["status"] == "done"
    assert (job["workspace"], job["workspace_reason"]) == (None, "workspace collection failed: boom")


def test_a_running_step_is_refreshed_periodically(jobs, tmp_path, monkeypatch):
    write_job(jobs, tmp_path)
    calls = []
    monkeypatch.setattr(fleetd, "WORKSPACE_REFRESH_SECONDS", 0.01)
    monkeypatch.setattr(fleetd, "refresh_workspace", lambda job_id, cwd: calls.append(job_id))
    deadline = time.monotonic() + 5
    with fleetd.WorkspaceWatch("job", str(tmp_path)):
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
    settled = len(calls)
    assert settled >= 2
    time.sleep(0.05)
    assert len(calls) == settled   # stopped with the step


def shown(monkeypatch, capsys, job, override_cli_method, cli_container):
    from fleet import cli
    override_cli_method('references', 'job', lambda reference: (SimpleNamespace(name="carbon"), "job"))
    monkeypatch.setattr(transport, "call", lambda host, arguments, **kwargs: job)
    monkeypatch.setattr(cli.console, "width", 200)
    cli.main(["show", "carbon:job"], container=cli_container)
    return capsys.readouterr().out


def shown_job(**fields):
    return {"id": "job", "description": "D", "status": "running", "agent": "claude", "project": "p", "cwd": "/w",
            "permission": "default", "session_id": None, "events": [], "todos": [],
            "steps": [{"index": 0, "title": "First", "status": "running", "result": None, "work_item": "w-1"}],
            **fields}


def test_show_names_the_repository_worktree_branch_and_step_work(monkeypatch, capsys, cli_container, override_cli_method):
    out = shown(monkeypatch, capsys, shown_job(workspace={
        "toplevel": "/w", "linked_worktree": True, "repository": "/repo", "branch": "feat/x", "detached": False,
        "head": "abc1234", "dirty": 3, "collected_at": 1.0}, workspace_reason=None), cli_container=cli_container, override_cli_method=override_cli_method)
    assert "repo /repo · worktree /w · feat/x @ abc1234 · 3 uncommitted" in out
    assert "[w-1]" in out


def test_show_says_why_a_workspace_is_unknown(monkeypatch, capsys, cli_container, override_cli_method):
    out = shown(monkeypatch, capsys, shown_job(workspace=None, workspace_reason="not a git repository"), cli_container=cli_container, override_cli_method=override_cli_method)
    assert "workspace unknown: not a git repository" in out
