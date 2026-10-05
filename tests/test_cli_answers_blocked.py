"""From the CLI, a reply to a job whose step is blocked answers that step: `fleet add` on the job and `fleet answer`
on the step's attention item both take the deck's path, so the step reads answered and the reply runs next."""
import json
import sys
from io import StringIO
from types import SimpleNamespace

import pytest

from fleet.container import configured_container
from fleet_cli import cli
from fleet import transport

from fleet.modules.attention.domain import BLOCKED_SOURCE, StreamContext
from fleet_worker import fleetd


@pytest.fixture
def worker(tmp_path, monkeypatch, override_cli_method, cli_container):
    """A job "job" on host "h" whose first of three steps ended blocked; fleetd runs in-process, holding runners."""
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path / "jobs")
    monkeypatch.setattr(fleetd, "launch_runner", lambda job_id: None)
    job = {"id": "job", "agent": "claude", "project": "p", "description": "Blocked", "cwd": str(tmp_path),
           "permission": "read-only", "created_at": 1, "runner_pid": None, "cancelled": False,
           "steps": [fleetd.make_step(index, prompt, None) for index, prompt in enumerate(["one", "two", "three"])]}
    job["steps"][0]["status"] = "blocked"
    (tmp_path / "jobs" / "job").mkdir(parents=True)
    (tmp_path / "jobs" / "job" / "job.json").write_text(json.dumps(job))
    host = SimpleNamespace(name="h")
    calls = []

    def call(target, arguments, stdin_text=None):
        calls.append(arguments)
        (tmp_path / "stdin").write_text(stdin_text or "")
        arguments = [str(tmp_path / "stdin") if argument == "/dev/stdin" else argument for argument in arguments]
        with monkeypatch.context() as patch:
            patch.setattr(sys, "argv", ["fleetd", *arguments])
            patch.setattr(sys, "stdin", StringIO(stdin_text or ""))
            patch.setattr(sys, "stdout", output := StringIO())
            try:
                fleetd.main()
            except SystemExit:
                pass
        reply = json.loads(output.getvalue().strip().splitlines()[-1])
        if "error" in reply:
            raise transport.FleetError(reply["error"])
        return reply
    monkeypatch.setattr(transport, "call", call)
    monkeypatch.setattr(transport, "host_by_name", lambda name: host)
    override_cli_method('references', 'job', lambda reference: (host, "job"))
    return calls


def add(*arguments):
    cli.main(["add", "h:job", *arguments])


def steps():
    return [(step.get("title"), step["prompt"], step["status"], step.get("answered_by"))
            for step in fleetd.read_job("job")["steps"]]


def blocked_item(project_id):
    return configured_container().initialized_attention().raise_item(project=project_id, kind='blocker', owner='user', source=BLOCKED_SOURCE, source_reference='h:job:0', headline='step 1 asks: which one?', context_reference='job:h:job', actor='fleet', stream_context=StreamContext(owner_type='job', owner_id='job', host='h', project='p', project_id=project_id, source=BLOCKED_SOURCE, summary='blocked', since=None, step=0))


def test_fleet_add_answers_the_waiting_step(worker, capsys):
    add("-s", "Use the second.")
    assert "answered; step 1 continues as step 4" in capsys.readouterr().out
    assert steps() == [("one", "one", "blocked", 3), ("two", "two", "pending", None),
                       ("three", "three", "pending", None),
                       ("Answer to step 1", "Use the second.", "pending", None)]
    assert fleetd.next_step(fleetd.read_job("job"))["index"] == 3
    add("-s", "Then tidy up.")   # nothing waits any more: appended
    assert steps()[-1] == ("Then tidy up.", "Then tidy up.", "pending", None)
    assert "answered" not in capsys.readouterr().out


def test_a_retried_answer_is_added_once(worker, capsys, *, cli_container):
    for _ in range(2):   # the second is a retry after a lost reply
        cli.answer_waiting_step(SimpleNamespace(name="h"), "job", 0, [{"prompt": "Use the second."}], "user", container=cli_container)
        assert "answered; step 1 continues as step 4" in capsys.readouterr().out
    assert len(steps()) == 4


def test_no_answer_just_appends(worker):
    add("-s", "Unrelated.", "--no-answer")
    assert steps()[0][3] is None and steps()[-1] == ("Unrelated.", "Unrelated.", "pending", None)
    assert fleetd.derive_status(fleetd.read_job("job")) == "blocked"


def test_fleet_add_resolves_the_steps_attention_item_with_the_decks_key(worker, project_id, capsys):
    item = blocked_item(project_id)
    add("-s", "Use the second.")
    assert f"--key {item.id}:answer" in " ".join(worker[-1])
    resolved = configured_container().initialized_attention().get(item.id)
    assert (resolved.state, resolved.resolution_details) == ("resolved", "answered; step 1 continues as step 4")


def test_fleet_answer_on_a_blocked_step_takes_the_decks_path(worker, project_id, capsys):
    item = blocked_item(project_id)
    cli.main(["answer", item.id, "Use the second."])
    assert json.loads(capsys.readouterr().out) == {"id": item.id,
                                                   "resolution": "answered; step 1 continues as step 4"}
    assert steps()[0][3] == 3 and steps()[-1] == ("Answer to step 1", "Use the second.", "pending", None)
    assert configured_container().initialized_attention().get(item.id).state == "resolved"
    [record] = configured_container().decisions().list()
    assert (record.attention_item, record.answer, record.actor) == (item.id, 'Use the second.', 'user')
    assert fleetd.derive_status(fleetd.read_job("job")) == "queued"
