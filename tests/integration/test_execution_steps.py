"""Step evidence persists independently from the worker job and is audited only on change."""

import json

import pytest

from fleet import composition
from fleet.web.ingester import observe_runs


def test_ingester_persists_steps_once_with_git_and_legacy_reason():
    store = composition.open_store()
    execution = composition.open_execution(store)
    library = composition.open_library(store)
    git = {"base": "a" * 40, "head": "b" * 40, "commit_count": 1,
           "commits": [{"sha": "b" * 40, "at": 2, "subject": "Ship"}], "pushes": [], "base_is_ancestor": True}
    step = {"index": 0, "title": "Implement", "status": "done", "started_at": 1, "finished_at": 2, "git": git}
    legacy = {"index": 1, "title": "Legacy", "status": "done", "started_at": 2, "finished_at": 3}
    job = {"id": "job", "status": "done", "agent": "codex", "steps": [step, legacy], "updated_at": 3}
    host = {"name": "carbon", "ok": True, "jobs": {"job": job}}
    observe_runs(execution, library, host)
    run, = execution.runs()
    records = execution.steps(run.id)
    assert records[0] == {"index": 0, "title": "Implement", "status": "done",
                          "start": "1970-01-01T00:00:01+00:00", "end": "1970-01-01T00:00:02+00:00",
                          "work_item": None, "git": git}
    assert records[1]["git"] == {"reason": "not recorded: the worker did not report per-step git"}
    history = [entry for entry in store.history_after(0) if ":step:" in entry["subject"]]
    assert [entry["subject"] for entry in history] == [f"execution:run:{run.id}:step:0", f"execution:run:{run.id}:step:1"]
    assert all(entry["actor"] == "fleetd" for entry in history)
    sequence = store.latest_sequence()
    for _ in range(100):
        observe_runs(execution, library, host)
    assert store.latest_sequence() == sequence
    step["git"] = {**git, "pushes": [{"ref": "refs/remotes/origin/main", "old": "a" * 40, "new": "b" * 40, "at": 2}]}
    observe_runs(execution, library, host)
    changed, = store.history_after(sequence)
    assert changed["subject"] == f"execution:run:{run.id}:step:0"
    assert json.loads(changed["from"])["git"]["pushes"] == []
    assert json.loads(changed["to"])["git"]["pushes"] == step["git"]["pushes"]
    host["jobs"] = {}  # removing the worker's job does not remove its stored steps
    observe_runs(execution, library, host)
    assert composition.open_execution(composition.open_store(store.path)).steps(run.id)[0]["git"] == step["git"]


def test_step_writes_validate_run_and_roll_back_an_invalid_index():
    store = composition.open_store()
    execution = composition.open_execution(store)
    with pytest.raises(LookupError):
        execution.observe_steps("missing", [{"index": 0}])
    run = execution.record_observed("carbon", {"id": "job"})
    sequence = store.latest_sequence()
    with pytest.raises(ValueError, match="nonnegative integer"):
        execution.observe_steps(run.id, [{"index": 0, "title": "Valid"}, {"index": -1}])
    assert execution.steps(run.id) == [] and store.latest_sequence() == sequence
