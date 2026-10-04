from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fleet.container import configured_container
from fleet.modules.execution import ExecutionFacade, Run
from fleet.projections.bench import bench_state


def test_bench_records_unknown_and_expired_actions_with_pinned_clock():
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    run = Run("r", "a", "worker", "j", "codex", "running", None, None, None, now,
              current_action="read", action_observed_at=now)
    execution = ExecutionFacade(SimpleNamespace(runs=lambda: [run]), None, clock=lambda: now)
    execution.observe_host('worker', reachable=True)
    for seconds, expected in [(0, "current"), (20, "current"), (21, "stale")]:
        execution.clock = lambda: now + timedelta(seconds=seconds)
        projected = execution.run_activity(run)
        document = {"project": "p", "work_items": [{"id": "m", "kind": "milestone", "title": "M",
            "children": [], "runs": [{"id": "r", "host": "worker", "status": "running", **projected}], "steps": [],
            "criteria": [], "progress": {}, "summary": None, "attention": [], "library": []}]}
        agent, = bench_state(document, "m")["agents"]
        assert agent["action_glyph"] == "read"
        assert agent["action_freshness"] == expected
        assert agent["action_observed_at"] == now.isoformat()
    unknown = execution.run_activity(replace(run, current_action=None, action_observed_at=None))
    assert unknown == {"action_glyph": None, "action_observed_at": None, "action_freshness": "unknown"}


def test_live_host_keeps_old_action_current(tmp_path):

    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    store = configured_container(path=tmp_path / 'clock.db', clock=lambda : now).store()
    execution = configured_container(store).execution()
    run = Run('r', 'a', 'worker', 'j', 'codex', 'running', None, None, None, now,
              current_action='test', action_observed_at=now - timedelta(seconds=30))
    before = store.latest_sequence()
    execution.observe_host('worker', reachable=True)
    execution.observe_host('worker', reachable=True)
    assert execution.run_activity(run)['action_freshness'] == 'current'
    assert store.latest_sequence() == before
    now += timedelta(seconds=21)
    assert execution.run_activity(run)['action_freshness'] == 'stale'
