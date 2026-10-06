"""Queued prompt editing through the public library, CLI and real worker."""
import json
import sys

import pytest

from fleet.api import FleetError, Host
from fleet.container import configured_container
from fleet_cli import cli
from fleet_worker import fleetd


@pytest.fixture
def queued_job(tmp_path, monkeypatch):
    home = tmp_path / "worker"
    monkeypatch.setenv("FLEET_HOME", str(home))
    monkeypatch.setenv("FLEET_FLEETD_PATH", fleetd.__file__)
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", home / "jobs")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"hosts": {"local": {"ssh": None, "python": sys.executable}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    steps = tmp_path / "steps.json"
    steps.write_text(json.dumps([{"prompt": "first", "title": "First"},
                                 {"prompt": "obsolete instruction", "title": "Second", "work_item": "w-1"}]))
    container = configured_container()
    host = Host("local", None, sys.executable)
    container.transport().call(host, ["create", "--id", "job", "--project", "p", "--description", "Edit test",
                                    "--agent", "codex", "--cwd", str(tmp_path), "--steps-file", str(steps), "--hold"])
    return container, host, home


def test_library_replaces_queued_prompt_and_readable_brief(queued_job):
    container, host, home = queued_job
    prompt = "Corrected instruction\nWait for the build before reporting done."
    result = container.jobs().edit_step(host, "job", 2, prompt, actor="codex")
    assert result == {"job": "job", "step": 1, "status": "edited", "prompt": prompt}
    assert (home / "jobs/job/brief-1.md").read_text() == prompt
    job = fleetd.read_job("job")
    assert [(s["index"], s["title"], s["status"]) for s in job["steps"]] == [
        (0, "First", "pending"), (1, "Second", "pending")]
    assert job["steps"][1]["work_item"] == "w-1"
    assert job["steps"][1]["prompt"] == prompt
    event = next(e for e in fleetd.read_events("job", 100) if e["kind"] == "step_edit")
    assert (event["actor"], event["previous_prompt"], event["prompt"]) == ("codex", "obsolete instruction", prompt)


@pytest.mark.parametrize("source", ["text", "file"])
def test_cli_edits_one_based_step_from_text_or_file(queued_job, tmp_path, source, capsys):
    container, host, home = queued_job
    prompt = "Corrected café prompt with quotes: 'ready'\nKeep this newline.\n"
    arguments = ["step", "edit", "local:job", "2", "--actor", "codex"]
    if source == "text":
        arguments += ["--text", prompt]
    else:
        path = tmp_path / "prompt.md"
        path.write_text(prompt)
        arguments += ["--file", str(path)]
    cli.main(arguments, container=container)
    assert "edited step 2" in capsys.readouterr().out
    assert (home / "jobs/job/brief-1.md").read_text() == prompt
    assert fleetd.read_job("job")["steps"][0]["prompt"] == "first"


@pytest.mark.parametrize("status,started_at", [
    ("running", None), ("done", None), ("failed", None), ("blocked", None), ("cancelled", None),
    ("pending", 123), ("pending", 0),
])
def test_worker_refuses_nonqueued_or_previously_started_steps(queued_job, status, started_at):
    container, host, home = queued_job
    with fleetd.locked_job("job") as job:
        job["steps"][1].update(status=status, started_at=started_at)
    before = (home / "jobs/job/job.json").read_bytes()
    with pytest.raises(FleetError, match="only pending steps that have never started"):
        container.jobs().edit_step(host, "job", 2, "new instruction", actor="codex")
    assert (home / "jobs/job/job.json").read_bytes() == before
    assert (home / "jobs/job/brief-1.md").read_text() == "obsolete instruction"


@pytest.mark.parametrize("number", [0, -1, 3])
def test_invalid_step_number_is_refused_without_editing_another_step(queued_job, number):
    container, host, home = queued_job
    with pytest.raises(FleetError, match="positive integer|job has no step"):
        container.jobs().edit_step(host, "job", number, "new instruction", actor="codex")
    assert [s["prompt"] for s in fleetd.read_job("job")["steps"]] == ["first", "obsolete instruction"]


def test_empty_prompt_is_refused_and_repeated_edit_has_one_audit_event(queued_job):
    container, host, home = queued_job
    with pytest.raises(FleetError, match="prompt must not be empty"):
        container.jobs().edit_step(host, "job", 2, " \n ", actor="codex")
    for _ in range(2):
        container.jobs().edit_step(host, "job", 2, "corrected", actor="codex")
    assert len([e for e in fleetd.read_events("job", 100) if e["kind"] == "step_edit"]) == 1


def test_live_runner_refuses_active_edit_and_executes_corrected_next_prompt(queued_job, tmp_path):
    import subprocess
    import time

    container, host, home = queued_job
    gate = tmp_path / "release"
    ready = tmp_path / "ready"
    prompts = tmp_path / "received.jsonl"
    runtime = tmp_path / "codex"
    runtime.write_text(f'''#!{sys.executable}
import json, sys, time
from pathlib import Path
with Path({str(prompts)!r}).open("a") as output:
    output.write(json.dumps(sys.argv[-1]) + "\\n")
Path({str(ready)!r}).touch()
deadline = time.monotonic() + 10
while not Path({str(gate)!r}).exists():
    if time.monotonic() > deadline:
        raise SystemExit("test gate was not released")
    time.sleep(0.01)
print(json.dumps({{"type": "thread.started", "thread_id": "session"}}))
print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "FLEET_STATUS: done"}}}}))
print(json.dumps({{"type": "turn.completed"}}))
''')
    runtime.chmod(0o755)
    (home / "config.json").write_text(json.dumps({"codex": str(runtime)}))
    runner = subprocess.Popen(host.fleetd_command(["_run", "job"]), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "test runtime did not start"
        with pytest.raises(FleetError, match="cannot be edited"):
            container.jobs().edit_step(host, "job", 1, "change active work", actor="codex")
        container.jobs().edit_step(host, "job", 2, "corrected queued work", actor="codex")
    finally:
        gate.touch()
        stdout, stderr = runner.communicate(timeout=15)
    assert runner.returncode == 0, (stdout, stderr)
    received = [json.loads(line) for line in prompts.read_text().splitlines()]
    assert len(received) == 2
    assert "corrected queued work" in received[1]
    assert "obsolete instruction" not in received[1]
    assert [s["status"] for s in container.jobs().show(host, "job")["steps"]] == ["done", "done"]


def test_edit_preserves_original_dispatch_identity_on_create_replay(queued_job):
    container, host, home = queued_job
    arguments = ["create", "--id", "tracked", "--run-id", "r", "--fingerprint", "f", "--schema-version", "4",
                 "--project", "p", "--description", "Tracked", "--agent", "codex", "--cwd", str(home.parent),
                 "--steps-file", str(home.parent / "steps.json"), "--hold"]
    container.transport().call(host, arguments)
    before = fleetd.read_job("tracked")
    container.jobs().edit_step(host, "tracked", 2, "corrected tracked prompt", actor="codex")
    container.transport().call(host, arguments)
    after = fleetd.read_job("tracked")
    assert after["steps"][1]["prompt"] == "corrected tracked prompt"
    assert (after["run_id"], after["fingerprint"], after["definition_fingerprint"]) == (
        before["run_id"], before["fingerprint"], before["definition_fingerprint"])


def test_worker_rejects_empty_replacement_when_called_directly(queued_job):
    container, host, home = queued_job
    with pytest.raises(FleetError, match="prompt must not be empty"):
        container.transport().call(host, ["edit-step", "job", "1", "--actor", "codex"], stdin_text=" \n")
    assert (home / "jobs/job/brief-1.md").read_text() == "obsolete instruction"


def test_keyed_add_replay_preserves_an_edited_queued_step(queued_job):
    container, host, home = queued_job
    payload = json.dumps([{"prompt": "original follow-up", "title": "Follow-up"}])
    arguments = ["add", "job", "--steps-file", "/dev/stdin", "--key", "follow-up", "--schema-version", "1", "--hold"]
    container.transport().call(host, arguments, stdin_text=payload)
    container.jobs().edit_step(host, "job", 3, "corrected follow-up", actor="codex")
    container.transport().call(host, arguments, stdin_text=payload)
    assert [s["prompt"] for s in fleetd.read_job("job")["steps"]] == [
        "first", "obsolete instruction", "corrected follow-up"]
    with pytest.raises(FleetError, match="add key has changed payload"):
        container.transport().call(host, arguments, stdin_text=json.dumps([{"prompt": "different follow-up", "title": "Follow-up"}]))


def test_delivered_answer_replay_uses_original_payload_after_edit(queued_job):
    container, host, home = queued_job
    with fleetd.locked_job("job") as job:
        job["steps"][1]["delivery_key"] = "answer"
    container.jobs().edit_step(host, "job", 2, "corrected answer", actor="codex")
    with fleetd.locked_job("job") as job:
        job["steps"][1].update(status="done", started_at=123)
    arguments = ["deliver", "job", "--schema-version", "1", "--key", "answer"]
    assert container.transport().call(host, arguments, stdin_text="obsolete instruction")["status"] == "applied"
    with pytest.raises(FleetError, match="delivery key has changed payload"):
        container.transport().call(host, arguments, stdin_text="different answer")


def test_missing_start_record_is_refused_instead_of_assumed_unstarted(queued_job):
    container, host, home = queued_job
    with fleetd.locked_job("job") as job:
        del job["steps"][1]["started_at"]
    with pytest.raises(FleetError, match="start timestamp is missing"):
        container.jobs().edit_step(host, "job", 2, "new instruction", actor="codex")
    assert (home / "jobs/job/brief-1.md").read_text() == "obsolete instruction"
