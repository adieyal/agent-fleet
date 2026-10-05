"""fleet's hooks in every interactive Claude session: what they record, and installing them into settings.json."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fleet.remote import fleetd

FLEETD = str(Path(fleetd.__file__).resolve())
QUESTION = {"questions": [{"header": "Probe run", "multiSelect": False,
                           "question": "The agent-friendliness probe needs a live site. How should I run it?",
                           "options": [{"label": "Staging", "description": "Run against staging now"},
                                       {"label": "Skip", "description": "Leave the probe for later"}]}]}
# Shaped like a real user settings file that already has hooks of its own.
EXISTING = {
    "model": "opus", "permissions": {"allow": ["Bash(git status:*)"]}, "statusLine": {"type": "command", "command": "~/bin/status"},
    "hooks": {
        "Notification": [{"matcher": "", "hooks": [{"type": "command", "command": "notify-send Claude"}]}],
        "PreCompact": [{"matcher": "auto", "hooks": [{"type": "command", "command": "~/bin/save-context"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/bin/guard-bash"}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": "~/bin/greet", "timeout": 5}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "~/bin/chime"}]}],
    },
}


def hook(event, tool="AskUserQuestion", tool_input=QUESTION, session="s1", cwd="/srv/restoke"):
    return {"hook_event_name": event, "session_id": session, "cwd": cwd, "tool_name": tool, "tool_input": tool_input}


@pytest.fixture
def home(tmp_path):
    """A throwaway HOME: its ~/.claude/settings.json and a FLEET_HOME for the fleetd that installs the hooks."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / "fleet").mkdir()
    return home


def fleetd_run(home, *arguments, stdin=None, **environment):
    env = {**os.environ, "HOME": str(home), "FLEET_HOME": str(home / "fleet"), **environment}
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("FLEET_JOB_ID", None) if "FLEET_JOB_ID" not in environment else None
    return subprocess.run([sys.executable, FLEETD, *arguments], input=stdin, env=env, capture_output=True,
                          text=True, timeout=30)


def settings(home):
    return json.loads((home / ".claude" / "settings.json").read_text())


def installed_command(home):
    [group] = [group for group in settings(home)["hooks"]["PermissionRequest"] if fleetd.SESSION_HOOK_MARK in json.dumps(group)]
    return group["hooks"][0]["command"]


def run_hook(home, command, record, **environment):
    env = {**os.environ, "HOME": str(home), **environment}
    env.pop("FLEET_JOB_ID", None) if "FLEET_JOB_ID" not in environment else None
    return subprocess.run(["sh", "-c", command], input=json.dumps(record), env=env, capture_output=True,
                          text=True, timeout=30)


def observations(home):
    directory = home / "fleet" / "input-observations"
    return [record for path in sorted(directory.glob("*.json")) for record in json.loads(path.read_text())]


def test_a_question_is_recorded_until_the_session_answers_it(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    fleetd.record_input_hook(hook("PreToolUse", tool="Bash", tool_input={"command": "ls"}), project="restoke")
    assert fleetd.input_observations() == []   # only AskUserQuestion is caught before it runs
    for _ in range(3):
        fleetd.record_input_hook(hook("PreToolUse"), project="restoke")
    [asked] = fleetd.input_observations()
    assert (asked["kind"], asked["reason"], asked["source_event"]) == ("input_requested", "question", "PreToolUse")
    assert (asked["owner_type"], asked["session_id"], asked["cwd"]) == ("session", "s1", "/srv/restoke")
    assert asked["request"]["questions"] == [{
        "header": "Probe run", "multi_select": False,
        "question": "The agent-friendliness probe needs a live site. How should I run it?",
        "options": [{"label": "Staging", "description": "Run against staging now"},
                    {"label": "Skip", "description": "Leave the probe for later"}]}]
    fleetd.record_input_hook(hook("PostToolUse"), project="restoke")
    [answered] = fleetd.input_observations()
    assert answered["kind"] == "input_cleared" and answered["source_event_id"] == asked["source_event_id"]


def test_install_merges_into_existing_hooks_and_uninstall_takes_out_only_its_own(home):
    path = home / ".claude" / "settings.json"
    path.write_text(json.dumps(EXISTING, indent=4))
    path.chmod(0o640)
    for _ in range(2):   # installing again changes nothing
        result = fleetd_run(home, "session-hooks", "install")
        assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["events"] == ["PermissionRequest", "PostToolUse", "PreToolUse"]
    merged = settings(home)
    assert {key: value for key, value in merged.items() if key != "hooks"} == {
        key: value for key, value in EXISTING.items() if key != "hooks"}
    for event, groups in EXISTING["hooks"].items():
        assert merged["hooks"][event][:len(groups)] == groups   # every existing hook kept, in order
    ours = {event: [group for group in groups if fleetd.SESSION_HOOK_MARK in json.dumps(group)]
            for event, groups in merged["hooks"].items()}
    assert {event: len(groups) for event, groups in ours.items() if groups} == {
        "PermissionRequest": 1, "PreToolUse": 1, "PostToolUse": 1}
    assert ours["PreToolUse"][0]["matcher"] == "AskUserQuestion"
    assert path.stat().st_mode & 0o777 == 0o640
    assert [name for name in os.listdir(path.parent)] == ["settings.json"]   # no temporary left behind
    assert fleetd_run(home, "session-hooks", "uninstall").returncode == 0
    assert settings(home) == EXISTING


def test_install_creates_settings_and_keeps_a_symlinked_file_a_symlink(home, tmp_path):
    assert fleetd_run(home, "session-hooks", "install").returncode == 0
    assert set(settings(home)["hooks"]) == {"PermissionRequest", "PreToolUse", "PostToolUse"}
    assert fleetd_run(home, "session-hooks", "uninstall").returncode == 0
    assert settings(home) == {}
    dotfiles = tmp_path / "dotfiles" / "claude-settings.json"
    dotfiles.parent.mkdir()
    dotfiles.write_text(json.dumps(EXISTING))
    link = home / ".claude" / "settings.json"
    link.unlink()
    link.symlink_to(dotfiles)
    assert fleetd_run(home, "session-hooks", "install").returncode == 0
    assert link.is_symlink() and "PermissionRequest" in json.loads(dotfiles.read_text())["hooks"]


def test_a_settings_file_that_is_not_json_is_left_alone(home):
    path = home / ".claude" / "settings.json"
    path.write_text('{"hooks": {,}')
    result = fleetd_run(home, "session-hooks", "install")
    assert result.returncode != 0 and "not valid JSON" in result.stdout
    assert path.read_text() == '{"hooks": {,}'


def test_the_installed_hook_records_sessions_and_leaves_jobs_to_their_own_hook(home):
    assert fleetd_run(home, "session-hooks", "install").returncode == 0
    command = installed_command(home)
    for event in ("PreToolUse", "PermissionRequest"):
        record = hook(event) if event == "PreToolUse" else hook(event, "Bash", {"command": "make deploy"})
        result = run_hook(home, command, record)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    asked, permission = observations(home)
    assert (asked["reason"], asked["project"], asked["cwd"]) == ("question", "restoke", "/srv/restoke")
    assert (permission["reason"], permission["owner_type"]) == ("permission", "session")
    # Inside a fleet job both the global hook and the job's own --settings hook run: only the job's records.
    job_command = fleetd.input_hook_settings("restoke", "job1", 0)["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
    job_command = job_command.replace(f"FLEET_HOME={fleetd.FLEET_HOME}", f"FLEET_HOME={home / 'fleet'}")
    record = hook("PermissionRequest", "Bash", {"command": "pytest -q"}, session="job-session")
    for each in (command, job_command):
        assert run_hook(home, each, record, FLEET_JOB_ID="job1").returncode == 0
    [from_job] = [item for item in observations(home) if item["session_id"] == "job-session"]
    assert (from_job["owner_type"], from_job["job_id"], from_job["step_index"]) == ("job", "job1", 0)


def test_the_installed_hook_is_a_silent_no_op_once_fleet_is_gone(home):
    assert fleetd_run(home, "session-hooks", "install").returncode == 0
    command = installed_command(home)
    (home / "fleet").rename(home / "fleet-removed")
    result = run_hook(home, command, hook("PreToolUse"))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (home / "fleet").exists()
    broken = command.replace(FLEETD, str(home / "missing-fleetd.py")).replace(str(home / "fleet"), str(home / "fleet-removed"))
    assert run_hook(home, broken, hook("PreToolUse")).returncode == 0
    assert not (home / "fleet-removed" / "input-observations").exists()


def test_fleet_hooks_installs_through_the_host_transport(home, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"hosts": {"home": {"python": sys.executable}}}))
    env = {**os.environ, "HOME": str(home), "FLEET_CONFIG": str(config), "FLEET_FLEETD_PATH": FLEETD,
           "FLEET_REMOTE_HOME": str(home / "fleet")}
    env.pop("CLAUDE_CONFIG_DIR", None)
    (home / ".claude" / "settings.json").write_text(json.dumps(EXISTING))
    for action in ("install", "uninstall"):
        result = subprocess.run([sys.executable, "-m", "fleet.cli", "hooks", action, "home"], env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert str(home / ".claude" / "settings.json") in "".join(result.stdout.split())   # however it wraps
        if action == "install":
            assert fleetd.SESSION_HOOK_MARK in json.dumps(settings(home))
            assert f"FLEET_HOME={home / 'fleet'}" in installed_command(home)
    assert settings(home) == EXISTING
