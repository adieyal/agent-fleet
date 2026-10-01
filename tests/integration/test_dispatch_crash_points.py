"""Kill the controller at durable boundaries; keep the worker's files alive."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest

from fleet import composition
from fleet.errors import FleetError
from fleet.remote import fleetd


CONTROLLER = '''
import sys
from fleet import composition
from fleet.infrastructure.sqlite.execution import ExecutionRepository
from fleet.remote import fleetd

point, item, payload = sys.argv[1:]
import json
payload = json.loads(payload)
def stop():
    print("crash-point", flush=True)
    sys.stdin.readline()
if point.startswith("transaction:"):
    name = point.split(":")[1]
    original = getattr(ExecutionRepository, name)
    def write(self, *args):
        original(self, *args)
        stop()
    setattr(ExecutionRepository, name, write)
execution = composition.open_execution()
run = execution.dispatch(item, host="local", runtime="codex", payload=payload,
    actor="user", reason="crash test", idempotency_key="crash").run
if point == "claim":
    stop()
def call(arguments, stdin):
    import contextlib, io
    if arguments[0] == "create":
        from pathlib import Path
        steps = Path(payload["cwd"]) / "steps.json"
        steps.write_text(stdin)
        arguments[arguments.index("--steps-file") + 1] = str(steps)
    output = io.StringIO()
    sys.argv = ["fleetd", *arguments]
    sys.stdin = io.StringIO(stdin or "")
    with contextlib.redirect_stdout(output):
        fleetd.main()
    if arguments[0] == point:
        # Restore the pipe used to wait for SIGKILL.
        sys.stdin = sys.__stdin__
        stop()
    return json.loads(output.getvalue())
def launch(job):
    with open(fleetd.JOBS_DIRECTORY.parent / "starts", "a") as stream:
        stream.write(job + "\\n")
fleetd.launch_runner = launch
execution.deliver(run, call, lambda *args: None)
'''


def worker_call(arguments, stdin):
    result = subprocess.run([sys.executable, fleetd.__file__, *arguments], input=stdin,
                            capture_output=True, text=True, timeout=10)
    reply = json.loads(result.stdout)
    if result.returncode:
        raise FleetError(reply["error"])
    return reply


@pytest.mark.parametrize("point", ["claim", "create", "start", "transaction:save_action",
                                   "transaction:save_run", "transaction:save_claim",
                                   "transaction:save_request"])
def test_killed_dispatch_reconciles_one_job_and_claim(tmp_path, point):
    store = composition.open_store()
    composition.open_workspace(store)
    item = composition.open_work(store).add(project="p", title="Epic", goal="Ship", actor="user")
    payload = {"cwd": str(tmp_path), "arguments": ["create", "--project", "p", "--description", "Crash test",
        "--agent", "codex", "--cwd", str(tmp_path), "--permission", "read-only",
        "--steps-file", "/dev/stdin", "--hold"], "steps": ["Ship"], "context": [], "hold": False}
    sequence = store.latest_sequence()
    process = subprocess.Popen([sys.executable, "-c", CONTROLLER, point, item.id, json.dumps(payload)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 10)[0], "controller never reached crash point"
        assert process.stdout.readline().strip() == "crash-point"
        process.kill()
        _, error = process.communicate(timeout=10)
        assert process.returncode == -signal.SIGKILL, error
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)

    execution = composition.open_execution(composition.open_store())
    if point.startswith("transaction:"):
        assert execution.actions() == execution.runs() == execution.claims() == []
        assert store.latest_sequence() == sequence
    else:
        run, = execution.runs()
        claim, = execution.claims()
        assert claim.run == run.id and claim.action == run.action and claim.active
    intent = execution.dispatch(item.id, host="local", runtime="codex", payload=payload,
        actor="user", reason="crash test", idempotency_key="crash")
    assert intent.created == point.startswith("transaction:")
    changes = store.history_after(sequence)
    assert len(changes) == (5 if point == 'start' else 4)
    if point == 'start':
        observation = changes[-1]
        assert observation['subject'] == f'execution:run:{intent.run.id}'
        assert observation['actor'] == 'fleetd'
        assert json.loads(observation['from'])['reason'] is None
        assert json.loads(observation['to'])['reason'] == 'queued'

    calls = []
    def call(arguments, stdin):
        calls.append(arguments[0])
        if arguments[0] == "start":
            # Use the real worker reservation without launching a runtime.
            program = '''
import sys
from fleet.remote import fleetd
def launch(job):
    with open(fleetd.JOBS_DIRECTORY.parent / "starts", "a") as stream:
        stream.write(job + "\\n")
fleetd.launch_runner = launch
fleetd.main()
'''
            result = subprocess.run([sys.executable, "-c", program, *arguments],
                                    capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        return worker_call(arguments, stdin)

    execution.deliver(intent.run, call, lambda *args: None, reconcile=True)
    assert calls[0] == "reconcile"
    jobs = list((Path(os.environ["FLEET_HOME"]) / "jobs").glob("*/job.json"))
    assert len(jobs) == 1
    job = json.loads(jobs[0].read_text())
    assert job["run_id"] == intent.run.id and job["id"] == intent.run.remote_job_id
    assert (Path(os.environ["FLEET_HOME"]) / "starts").read_text().splitlines() == [job["id"]]
    assert len(execution.actions()) == len(execution.runs()) == len(execution.claims()) == 1
    claim, = execution.claims()
    assert claim.active and claim.run == intent.run.id and claim.action == intent.run.action
    sequence = store.latest_sequence()
    execution.deliver(intent.run, call, lambda *args: None, reconcile=True)
    assert store.latest_sequence() == sequence
    assert (Path(os.environ["FLEET_HOME"]) / "starts").read_text().splitlines() == [job["id"]]


def test_absent_reconcile_is_versioned():
    arguments = ["reconcile", "missing", "--fingerprint", "digest", "--schema-version"]
    with pytest.raises(FleetError, match="unsupported dispatch schema version"):
        worker_call([*arguments, "3"], None)
    assert worker_call([*arguments, "4"], None) == {
        "schema_version": 4, "run_id": "missing", "fingerprint": "digest", "status": "absent"}


@pytest.mark.parametrize("fault", ["disconnect", "schema_version", "run_id", "fingerprint"])
def test_uncertain_or_mismatched_absence_never_creates(fault):
    execution = composition.open_execution()
    run = execution.dispatch(None, project="p", host="local", runtime="codex",
        payload={"cwd": "/repo"}, actor="user", reason="test", idempotency_key="request").run
    store = composition.open_store()
    sequence = store.latest_sequence()
    calls = []

    def call(arguments, stdin):
        calls.append(arguments[0])
        if fault == "disconnect":
            raise FleetError("disconnected")
        reply = {"schema_version": 4, "run_id": run.id, "status": "absent",
                 "fingerprint": arguments[arguments.index("--fingerprint") + 1]}
        reply[fault] = "wrong"
        return reply

    for _ in range(2):
        with pytest.raises(FleetError):
            execution.deliver(run, call, lambda *args: None, reconcile=True)
    assert calls == ["reconcile", "reconcile"]
    assert store.latest_sequence() == sequence
    assert execution.get_run(run.id).status == "unknown outcome"
    assert execution.claims()[0].active
