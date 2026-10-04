"""Per-step evidence comes from real temporary repositories and local bare remotes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import signal

import pytest

from fleet.remote import fleetd


def git(repository, *arguments):
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True,
                          text=True, env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
                                         "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org"}).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "core.hooksPath", "/dev/null")
    git(path, "commit", "--allow-empty", "-m", "Base")
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(path, "remote", "add", "origin", str(remote))
    git(path, "push", "origin", "main")
    return path


def test_commit_and_push_excludes_the_push_before_begin(repository):
    beginning = fleetd.begin_step_git(str(repository))
    git(repository, "commit", "--allow-empty", "-m", "A" * 100)
    head = git(repository, "rev-parse", "HEAD")
    git(repository, "push", "origin", "main")
    record = fleetd.end_step_git(str(repository), beginning)
    assert "reason" not in record
    assert len(record["base"]) == len(record["head"]) == 40
    assert record["branch"] == record["branch_end"] == "main"
    assert record["base_is_ancestor"] and record["commit_count"] == 1 and not record["truncated"]
    assert record["commits"] == [{"sha": head, "at": int(git(repository, "show", "-s", "--format=%at", head)),
                                  "subject": "A" * 72}]
    assert len(record["pushes"]) == 1
    assert record["pushes"][0] == {"ref": "refs/remotes/origin/main", "old": beginning["base"],
                                   "new": head, "at": record["pushes"][0]["at"]}
    assert not any(key.startswith("_") for key in record)


def test_linked_worktree_push_uses_common_logs_and_excludes_other_worktree(repository, tmp_path):
    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "-b", "own", str(linked))
    beginning = fleetd.begin_step_git(str(linked))
    git(repository, "commit", "--allow-empty", "-m", "Other work")
    other = git(repository, "rev-parse", "HEAD")
    git(repository, "push", "origin", "main")
    git(linked, "commit", "--allow-empty", "-m", "Own work")
    own = git(linked, "rev-parse", "HEAD")
    git(linked, "push", "origin", "own")
    record = fleetd.end_step_git(str(linked), beginning)
    assert "reason" not in record
    assert [entry["new"] for entry in record["pushes"]] == [own]
    assert record["pushes"][0]["ref"] == "refs/remotes/origin/own"
    assert other not in [entry["sha"] for entry in record["commits"]]


def test_prior_push_to_unchanged_head_is_not_counted_in_same_second(repository, monkeypatch):
    log = repository / ".git/logs/refs/remotes/origin/main"
    metadata = log.read_text().splitlines()[-1].split("\t", 1)[0]
    second = int(metadata.rsplit(" ", 2)[1])
    monkeypatch.setattr(fleetd, "now", lambda: second + 0.5)
    record = fleetd.end_step_git(str(repository), fleetd.begin_step_git(str(repository)))
    assert "reason" not in record
    assert record["head"] == record["base"] and record["commits"] == [] and record["pushes"] == []


def test_rewritten_history_and_detached_head_are_explicit(repository):
    beginning = fleetd.begin_step_git(str(repository))
    git(repository, "commit", "--amend", "--allow-empty", "-m", "Rewritten base")
    git(repository, "checkout", "--detach")
    record = fleetd.end_step_git(str(repository), beginning)
    assert "reason" not in record
    assert record["base_is_ancestor"] is False
    assert record["branch_end"] is None and record["commit_count"] == 1
    assert record["commits"][0]["subject"] == "Rewritten base"


def test_bounded_commits_still_attribute_a_push_to_an_omitted_commit(repository):
    beginning = fleetd.begin_step_git(str(repository))
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    head = beginning["base"]
    for index in range(105):
        head = git(repository, "commit-tree", tree, "-p", head, "-m", f"Commit {index}")
        git(repository, "update-ref", "refs/heads/main", head)
        if index == 0:
            first = head
            git(repository, "push", "origin", "main")
    record = fleetd.end_step_git(str(repository), beginning)
    assert "reason" not in record
    assert record["commit_count"] == 105 and len(record["commits"]) == 100 and record["truncated"]
    assert first not in [entry["sha"] for entry in record["commits"]]
    assert [entry["new"] for entry in record["pushes"]] == [first]


def test_non_git_unborn_and_capture_errors_have_reasons(tmp_path, monkeypatch):
    record = fleetd.begin_step_git(str(tmp_path))
    assert "not a git repository" in record["reason"]
    assert fleetd.end_step_git(str(tmp_path), record)["reason"] == record["reason"]
    git(tmp_path, "init")
    assert "reason" in fleetd.begin_step_git(str(tmp_path))

    def broken(*args):
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(fleetd, "git", broken)
    assert "timed out" in fleetd.begin_step_git(str(tmp_path))["reason"]


def test_runner_records_commit_and_push_in_streamed_step(repository, tmp_path, monkeypatch):
    home = Path(os.environ["FLEET_HOME"])
    directory = home / "jobs" / "job"
    directory.mkdir(parents=True)
    agent = tmp_path / "agent"
    agent.write_text(f"#!{sys.executable}\n" +
        "import subprocess, json\n"
        "subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.org', "
        "'commit', '--allow-empty', '-m', 'Agent commit'], check=True, stdout=subprocess.DEVNULL)\n"
        "subprocess.run(['git', 'push', 'origin', 'main'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'FLEET_STATUS: done'}))\n")
    agent.chmod(0o755)
    (home / "config.json").write_text(json.dumps({"claude": str(agent)}))
    definition = {"id": "job", "agent": "claude", "project": "p", "description": "Commit",
                  "cwd": str(repository), "permission": "default", "created_at": 1, "runner_pid": None,
                  "steps": [fleetd.make_step(0, "Commit", "Commit")]}
    (directory / "job.json").write_text(json.dumps(definition))
    result = subprocess.run([sys.executable, fleetd.__file__, "_run", "job"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    saved = json.loads((directory / "job.json").read_text())
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", home / "jobs")
    summary = fleetd.job_summary(saved, 0)
    record = summary["steps"][0]["git"]
    assert summary["status"] == "done" and "reason" not in record
    assert record["commit_count"] == 1 and record["commits"][0]["subject"] == "Agent commit"
    assert record["pushes"][0]["new"] == record["head"] == git(repository, "rev-parse", "HEAD")
    assert "_reflog_offsets" not in record
    from fleet import composition
    from fleet.composition import observe_runs

    store = composition.open_store()
    execution = composition.open_execution(store)
    observe_runs(execution, composition.open_library(store), {"name": "carbon", "ok": True, "jobs": {"job": summary}})
    run, = execution.runs()
    assert execution.steps(run.id)[0]["git"] == record


def test_shortened_reflog_reports_incomplete_capture(repository):
    beginning = fleetd.begin_step_git(str(repository))
    log = repository / ".git/logs/refs/remotes/origin/main"
    log.write_text("")
    record = fleetd.end_step_git(str(repository), beginning)
    assert "reflog shortened" in record["reason"]
    assert record["head"] == beginning["base"]


def test_runner_killed_mid_step_leaves_base_without_end(repository, tmp_path, monkeypatch):
    home = Path(os.environ["FLEET_HOME"])
    directory = home / "jobs" / "job"
    directory.mkdir(parents=True)
    agent = tmp_path / "agent"
    agent.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    agent.chmod(0o755)
    (home / "config.json").write_text(json.dumps({"claude": str(agent)}))
    definition = {"id": "job", "agent": "claude", "project": "p", "description": "Killed",
                  "cwd": str(repository), "permission": "default", "created_at": 1, "runner_pid": None,
                  "steps": [fleetd.make_step(0, "Wait", "Wait")]}
    (directory / "job.json").write_text(json.dumps(definition))
    process = subprocess.Popen([sys.executable, fleetd.__file__, "_run", "job"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            saved = json.loads((directory / "job.json").read_text())
            child = saved.get("agent_pid")
            if child:
                break
            if process.poll() is not None:
                pytest.fail(process.communicate()[1].decode())
            time.sleep(0.02)
        assert child is not None
        process.kill()
        process.wait(timeout=5)
        saved = json.loads((directory / "job.json").read_text())
        record = fleetd.step_git_record(saved["steps"][0])
        assert record["base"] == git(repository, "rev-parse", "HEAD")
        assert "head" not in record and "ended_at" not in record
        assert "_reflog_offsets" not in record
        assert fleetd.step_git_record(saved["steps"][0], "lost")["reason"] == "step ended without its runner; end not recorded"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child:
            os.kill(child, signal.SIGKILL)
        process.communicate(timeout=5)
