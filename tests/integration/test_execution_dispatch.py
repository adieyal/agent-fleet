import os
import select
import subprocess
import sys

import pytest

from fleet import composition
from fleet.modules.execution import JobObservation


def setup_dispatch():
    store = composition.open_store()
    workspace = composition.open_workspace(store)
    project = workspace.move_in(["one", "two"], "demo").project_id
    item = composition.open_work(store).add(project=project, title="Epic", goal="Ship", kind="epic", actor="user")
    return store, workspace, item, composition.open_execution(store)


def dispatch(execution, item, key="request", host="one"):
    return execution.dispatch(item.id, host=host, runtime="codex", payload={"cwd": "/repo", "steps": ["Ship"]},
                              actor="user", reason="manual", idempotency_key=key)


def test_dispatch_deduplicates_and_parallel_actions_serve_one_epic():
    store, workspace, item, execution = setup_dispatch()
    first = dispatch(execution, item)
    sequence = store.latest_sequence()
    repeat = dispatch(execution, item)
    assert repeat.run == first.run and not repeat.created
    assert store.latest_sequence() == sequence
    second = dispatch(execution, item, "other", "two")
    assert first.run.action != second.run.action
    assert [run.host for run in execution.runs()] == ["one", "two"]
    assert len(execution.claims()) == 2
    action = execution.actions()[0]
    assert (action.actor, action.dispatch_reason, action.idempotency_key) == ("user", "manual", "request")
    assert action.payload_fingerprint
    with pytest.raises(ValueError, match="payload"):
        dispatch(execution, item, host="two")
    workspace.shutter(item.project)
    with pytest.raises(ValueError, match="shuttered"):
        dispatch(execution, item, "closed")
    assert len(execution.runs()) == 2


def test_execution_gets_run_and_action_by_identity(monkeypatch):
    store, _, item, execution = setup_dispatch()
    first = dispatch(execution, item).run
    second = dispatch(execution, item, "other").run
    action = execution.actions()[0]
    sequence = store.latest_sequence()

    def no_scan():
        raise AssertionError("identity lookup must not scan all records")

    monkeypatch.setattr(execution.repository, "runs", no_scan)
    monkeypatch.setattr(execution.repository, "actions", no_scan)
    assert execution.get_run(first.id) == first
    assert execution.get_run(second.id) == second
    assert execution.get_action(first.action) == action
    with pytest.raises(LookupError, match="missing"):
        execution.get_run("missing")
    with pytest.raises(LookupError, match="missing"):
        execution.get_action("missing")
    assert store.latest_sequence() == sequence


@pytest.mark.parametrize("status", ["done", "failed", "cancelled", "lost"])
def test_claim_released_at_known_end_and_observations_do_not_churn(status):
    store, _, item, execution = setup_dispatch()
    run = dispatch(execution, item).run
    with pytest.raises(ValueError, match="unknown"):
        execution.retry(run.id, actor="user", idempotency_key="retry")
    observation = JobObservation(run.remote_job_id, status, "codex", None, None, None)
    ended = execution.observe(run.host, observation)
    assert ended.reason == ("lost" if status == "lost" else None)
    assert not execution.claims()[0].active
    sequence = store.latest_sequence()
    execution.observe(run.host, observation)
    assert store.latest_sequence() == sequence
    retry = execution.retry(run.id, actor="user", idempotency_key="retry")
    assert retry.run.action == run.action and retry.run.id != run.id
    assert execution.retry(run.id, actor="user", idempotency_key="retry").run == retry.run
    assert dispatch(execution, item).run.id == run.id


def test_explicit_resolution_allows_retry_and_is_idempotent():
    store, _, item, execution = setup_dispatch()
    run = dispatch(execution, item).run
    execution.resolve_unknown(run.id, actor="user")
    sequence = store.latest_sequence()
    execution.resolve_unknown(run.id, actor="user")
    assert store.latest_sequence() == sequence
    resolved = execution.runs()[0]
    assert resolved.status == "stopped" and resolved.reason == "resolved unknown"
    assert execution.retry(run.id, actor="user", idempotency_key="retry").created


def test_two_processes_claim_each_action_once(tmp_path):
    store, _, item, execution = setup_dispatch()
    sequence = store.latest_sequence()
    program = '''
import sys
from fleet import composition
execution = composition.open_execution()
print("ready", flush=True)
for i in range(24):
    sys.stdin.readline()
    result = execution.dispatch(sys.argv[1], host="one", runtime="codex", payload={"cwd":"/repo"},
                                actor="user", reason="manual", idempotency_key=str(i))
    print(result.run.id, flush=True)
'''
    processes = [subprocess.Popen([sys.executable, "-c", program, item.id], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ)
                 for _ in range(2)]
    def line(process):
        assert select.select([process.stdout], [], [], 10)[0], "writer did not respond"
        return process.stdout.readline().strip()

    try:
        for process in processes:
            assert line(process) == "ready"
        for _ in range(24):
            for process in processes:
                process.stdin.write("go\n")
                process.stdin.flush()
            assert line(processes[0]) == line(processes[1])
        for process in processes:
            output, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    assert len(execution.actions()) == len(execution.runs()) == len(execution.claims()) == 24
    assert len(store.history_after(sequence)) == 24 * 4


def test_unknown_retains_claim_and_retry_of_previous_end_cannot_duplicate():
    store, _, item, execution = setup_dispatch()
    first = dispatch(execution, item).run
    execution.observe(first.host, JobObservation(first.remote_job_id, "done", "codex", None, None, None))
    second = execution.retry(first.id, actor="user", idempotency_key="retry").run
    execution.observe(second.host, JobObservation(second.remote_job_id, "running", "codex", None, None, None))
    execution.unavailable(second.host)
    sequence = store.latest_sequence()
    execution.unavailable(second.host)
    assert store.latest_sequence() == sequence
    assert execution.claims()[-1].active
    assert execution.retry(first.id, actor="user", idempotency_key="another-retry").run.id == second.id
    execution.resolve_unknown(second.id, actor="user")
    assert execution.retry(first.id, actor="user", idempotency_key="another-retry").run.id == second.id


def test_dispatch_rolls_back_action_if_claim_write_fails(monkeypatch):
    from fleet.infrastructure.sqlite.execution import ExecutionRepository

    store, _, item, execution = setup_dispatch()
    sequence = store.latest_sequence()

    def fail(*args):
        raise RuntimeError("claim failed")

    monkeypatch.setattr(ExecutionRepository, "save_claim", fail)
    with pytest.raises(RuntimeError, match="claim failed"):
        dispatch(execution, item)
    assert execution.actions() == execution.runs() == execution.claims() == []
    assert store.latest_sequence() == sequence


def test_legacy_send_label_cannot_bypass_project_shutter():
    _, workspace, item, execution = setup_dispatch()
    workspace.shutter(item.project)
    with pytest.raises(ValueError, match="shuttered"):
        execution.dispatch(None, project="demo", host="one", runtime="codex", payload={"cwd": "/repo"},
                           actor="user", reason="manual", idempotency_key="legacy")
    assert execution.actions() == []
