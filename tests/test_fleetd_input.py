import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fleet.remote import fleetd


@pytest.mark.parametrize("job_id", [None, "job1"])
def test_permission_hooks_are_retained_and_correlated(tmp_path, monkeypatch, job_id):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    request = {"hook_event_name": "PermissionRequest", "session_id": "session1",
               "tool_name": "Bash", "tool_input": {"command": "touch marker"}}
    for _ in range(10):
        fleetd.record_input_hook(request, project="example", job_id=job_id, step_index=0)
    [waiting] = fleetd.input_observations()
    assert waiting["schema_version"] == 1
    assert waiting["kind"] == "input_requested"
    assert waiting["owner_type"] == ("job" if job_id else "session")
    assert waiting["job_id"] == job_id
    assert waiting["session_id"] == "session1"
    assert waiting["context_reference"]
    assert waiting["request"] == {"tool": "Bash", "description": "", "detail": "touch marker",
                                  "rules": ["Bash(touch:*)"]}
    fleetd.record_input_hook({**request, "hook_event_name": "Stop"}, project="example",
                            job_id=job_id, step_index=0)
    fleetd.record_input_hook({**request, "hook_event_name": "PostToolUse",
                             "tool_input": {"command": "unrelated"}}, project="example",
                            job_id=job_id, step_index=0)
    assert fleetd.input_observations() == [waiting]
    fleetd.record_input_hook({**request, "hook_event_name": "PostToolUse"}, project="example",
                            job_id=job_id, step_index=0)
    [resumed] = fleetd.input_observations()
    assert resumed["kind"] == "input_cleared"
    assert resumed["source_event_id"] == waiting["source_event_id"]
    fleetd.record_input_hook(request, project="example", job_id=job_id, step_index=0)
    assert len(fleetd.input_observations()) == 2


def test_jobs_install_hooks_and_stream_replays_them(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    job = {"id": "job1", "agent": "claude", "project": "example", "description": "probe",
           "permission": "default"}
    command = fleetd.agent_command(job, {"index": 0, "prompt": "probe"}, None)
    settings = json.loads(command[command.index("--settings") + 1])
    assert set(settings["hooks"]) == {"PermissionRequest", "PostToolUse"}
    assert "input-hook" in settings["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
    fleetd.record_input_hook({"hook_event_name": "PermissionRequest", "session_id": "s",
                             "tool_name": "Bash", "tool_input": {}}, project="example")
    messages = []
    monkeypatch.setattr(fleetd, "emit", messages.append)
    monkeypatch.setattr(fleetd.SessionTracker, "scan", lambda self: {})
    monkeypatch.setattr(fleetd.PipelineTracker, "scan", lambda self: [])
    monkeypatch.setattr(fleetd.time, "sleep", lambda _: (_ for _ in ()).throw(BrokenPipeError()))
    monkeypatch.setattr(fleetd.os, "close", lambda _: None)
    fleetd.command_stream(argparse.Namespace(since_hours=24, events=0, session_interval=1,
                                            heartbeat=1, interval=1))
    assert len([m for m in messages if m["type"] == "input_observation"]) == 1


def test_carbon_checkpoint_script_exists_and_parses():
    path = Path(__file__).resolve().parents[1] / "scripts/checks/waiting-for-input.sh"
    assert path.is_file()
    subprocess.run(["bash", "-n", str(path)], check=True)
    assert "--host carbon" in path.read_text()


def test_hook_command_receives_runtime_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    command = fleetd.input_hook_settings("project with spaces")["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
    result = subprocess.run(command, shell=True, input=json.dumps({
        "hook_event_name": "PermissionRequest", "session_id": "session1",
        "tool_name": "Bash", "tool_input": {"command": "touch marker"}}),
        text=True, capture_output=True, env=dict(os.environ), check=True)
    assert result.stdout == ""
    [observation] = fleetd.input_observations()
    assert observation["project"] == "project with spaces"


@pytest.mark.parametrize("tool, tool_input, rules", [
    ("Bash", {"command": "cd /srv && FOO=1 git status --short | head -5 > out.txt 2>&1"},
     ["Bash(cd:*)", "Bash(git status:*)", "Bash(head:*)"]),
    ("Bash", {"command": "npm run build; python3 -c 'print(1)'"}, ["Bash(npm run:*)", "Bash(python3:*)"]),
    ("Bash", {"command": "git -C /srv log"}, ["Bash(git:*)"]),
    ("Bash", {"command": "echo 'unbalanced"}, []),
    ("Read", {"file_path": "/etc/hosts"}, ["Read(//etc/hosts)"]),
    ("Edit", {"file_path": "notes.md"}, ["Edit(notes.md)"]),
    ("WebFetch", {"url": "https://docs.example.com/a"}, ["WebFetch(domain:docs.example.com)"]),
    ("mcp__docs__read", {}, ["mcp__docs__read"]),
])
def test_each_refused_request_names_the_rules_that_would_allow_it(tool, tool_input, rules):
    assert fleetd.permission_rules(tool, tool_input) == rules


@pytest.mark.parametrize("agent, stdin, error", [
    ("claude", "[]", "a JSON list of permission rules"),
    ("claude", '["Bash(ls:*)", "rm -rf /"]', "a JSON list of permission rules"),
    ("codex", '["Bash"]', "claude jobs only"),
])
def test_a_grant_that_cannot_apply_changes_nothing(tmp_path, monkeypatch, capsys, agent, stdin, error):
    import io
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    (tmp_path / "jobs" / "j1").mkdir(parents=True)
    job = {"id": "j1", "agent": agent, "allowed_tools": [], "steps": [{"index": 0}]}
    (tmp_path / "jobs" / "j1" / "job.json").write_text(json.dumps(job))
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit):
        fleetd.command_grant(argparse.Namespace(job="j1", step=0, key="k", schema_version=1))
    assert error in capsys.readouterr().out
    assert json.loads((tmp_path / "jobs" / "j1" / "job.json").read_text()) == job


STREAM = Path(__file__).parent / "fixtures" / "permission-denied-stream.jsonl"   # trimmed from a real probe job
CURL = {"command": "curl -s -o /dev/null -w '%{http_code}' https://example.com",
        "description": "Fetch example.com and print HTTP status code"}
PROBE = {"id": "probe", "project": "fleet-selftest", "cwd": "/tmp/fleet-allow-probe", "session_id": None}


def stream_records(keep=lambda record: True):
    return [record for record in map(json.loads, STREAM.read_text().splitlines()) if keep(record)]


def refusals_seen(records):
    tracker = fleetd.StreamRefusals(PROBE, 0)
    for record in records:
        tracker.consume(record)
    return [(item["owner_type"], item["job_id"], item["step_index"], item["kind"], item["request"]["rules"])
            for item in fleetd.input_observations()]


def test_a_refusal_reported_only_in_the_stream_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    assert refusals_seen(stream_records()) == [("job", "probe", 0, "input_requested", ["Bash(curl:*)"])]
    [observation] = fleetd.input_observations()
    assert observation["request"]["detail"] == CURL["command"]
    assert observation["session_id"] == "e01a0183-a092-4256-8d77-7e432ff21e68"


def test_the_results_permission_denials_are_the_backstop(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    only_result = stream_records(lambda record: record["type"] == "result")
    assert refusals_seen(only_result) == [("job", "probe", 0, "input_requested", ["Bash(curl:*)"])]


@pytest.mark.parametrize("hook_first", [True, False])
def test_a_refusal_the_hook_also_reports_is_one_entry(tmp_path, monkeypatch, hook_first):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    hook = {"hook_event_name": "PermissionRequest", "session_id": "e01a0183-a092-4256-8d77-7e432ff21e68",
            "cwd": "/tmp/fleet-allow-probe", "tool_name": "Bash", "tool_input": CURL}
    record_hook = lambda: fleetd.record_input_hook(hook, project="fleet-selftest", job_id="probe", step_index=0)
    if hook_first:
        record_hook()
    refusals_seen(stream_records())
    if not hook_first:
        record_hook()
    assert len(fleetd.input_observations()) == 1


def test_a_running_step_records_what_its_stream_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(fleetd, "FLEET_HOME", tmp_path)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    claude = tmp_path / "claude"
    claude.write_text(f"#!/bin/sh\ncat {STREAM}\n")
    claude.chmod(0o755)
    (tmp_path / "config.json").write_text(json.dumps({"claude": str(claude)}))
    job = {**PROBE, "agent": "claude", "description": "probe", "permission": "acceptEdits", "cwd": str(tmp_path),
           "steps": [fleetd.make_step(0, "run curl", None)]}
    (tmp_path / "jobs" / "probe").mkdir(parents=True)
    (tmp_path / "jobs" / "probe" / "job.json").write_text(json.dumps(job))
    fleetd.run_step(job, job["steps"][0])
    [observation] = fleetd.input_observations()
    assert (observation["job_id"], observation["step_index"], observation["request"]["rules"]) == (
        "probe", 0, ["Bash(curl:*)"])
