import argparse
import json
import os
from pathlib import Path
import subprocess

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
