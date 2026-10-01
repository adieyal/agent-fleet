from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from fleet.modules.attention import AttentionFacade, AttentionItem
from fleet.modules.work import Criterion, EvidenceSpecification, Relation, Summary, WorkFacade, WorkItem
from fleet.modules.execution import Action, ExecutionFacade, Run
from fleet.modules.library import LibraryEntry
from fleet.projections.project import project_status


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


class ReadWork:
    def __init__(self, items, criteria=(), summaries=(), relations=()):
        self.records = {"item": items, "criterion": criteria, "summary": summaries, "relation": relations}

    def list(self, kind):
        return self.records[kind]

    def get(self, kind, identity):
        return next(item for item in self.list(kind) if item.id == identity)


class ReadAttention:
    def __init__(self, items):
        self.items = items

    def list(self):
        return self.items


def item(identity, **changes):
    return replace(WorkItem(identity, "p", None, "task", identity, "Deliver", "none",
                            None, None, None, NOW, NOW), **changes)


def project(items, criteria=(), summaries=(), attention=(), runs=(), entries=(), host_observations=(), relations=()):
    # These ports expose reads only; any attempted write fails.
    work = WorkFacade(ReadWork(items, criteria, summaries, relations), None, lambda: NOW)
    alerts = AttentionFacade(ReadAttention(attention), lambda: NOW)
    activity = ExecutionFacade(None, None, clock=lambda: NOW)
    for host, observed in host_observations:
        activity.clock = lambda: observed
        activity.observe_host(host, reachable=True)
    activity.clock = lambda: NOW
    execution = SimpleNamespace(actions=lambda: [Action(run.action, "milestone", "linked") for run in runs],
                                runs=lambda: runs, run_activity=activity.run_activity)
    library = SimpleNamespace(list=lambda: entries)
    return project_status("p", work, alerts, execution, library, SimpleNamespace(list=lambda: []))


def test_progress_precedence_counts_only_direct_milestones_and_preserves_unknown():
    items = [item("epic", kind="epic"), item("done", parent="epic", kind="milestone", condition="complete"),
             item("planned", parent="epic", kind="milestone"),
             item("task", parent="epic", condition="complete"), item("unknown"),
             item("other", project="other")]
    criteria = [Criterion("c1", "epic", "Ignored for progress", "accepted", None, state="met"),
                Criterion("c2", "planned", "Reviewed", "judged", None, state="met"),
                Criterion("c3", "planned", "Approved", "accepted", None),
                Criterion("c4", "planned", "Tests", "checked", EvidenceSpecification("report"))]
    result = project(items, criteria)
    epic, unknown = result["work_items"]
    assert epic["progress"] == {"basis": "milestones", "complete": 1, "total": 2}
    planned = epic["children"][1]
    assert planned["progress"] == {"basis": "criteria", "complete": 1, "total": 3}
    assert epic["children"][2]["progress"] == {"basis": "unknown", "complete": None, "total": None}
    assert [c["verification"] for c in planned["criteria"]] == ["judged", "accepted", "checked"]
    assert unknown["progress"] == {"basis": "unknown", "complete": None, "total": None}
    assert unknown["summary"] is None
    assert unknown["next_step"] is None


def test_tree_summary_and_linked_open_attention_are_read_only():
    items = [item("root", kind="epic")]
    for depth in range(4):
        items.append(item(str(depth), parent=items[-1].id))
    items[-1] = replace(items[-1], condition="blocked", next_step="Ask user")
    summary = Summary("3", "Purpose", "Done", "Doing", "Next", "user", NOW)
    blocker = AttentionItem("a", "p", "3", None, "blocker", "user", "work", "3",
                            "Choose supplier", "work:3", "open", None, None, NOW)
    attention = [blocker, replace(blocker, id="resolved", state="resolved"),
                 replace(blocker, id="project", work_item=None),
                 replace(blocker, id="expired", state="snoozed", snooze_until=NOW-timedelta(days=1)),
                 replace(blocker, id="later", state="snoozed", snooze_until=NOW+timedelta(days=1)),
                 replace(blocker, id="other", project="other")]
    result = project(items, summaries=[summary], attention=attention)
    node = result["work_items"][0]
    for _ in range(4):
        node, = node["children"]
    assert node["condition"] == "blocked"
    assert node["next_step"] == "Ask user"
    assert node["summary"]["authoring_role"] == "user"
    assert node["summary"]["updated"] == NOW.isoformat()
    assert [a["id"] for a in node["attention"]] == ["a", "expired"]
    assert [a["id"] for a in result["attention"]] == ["project"]
    assert attention[3].state == "snoozed"


def test_empty_project_has_no_invented_progress():
    assert project([]) == {"project": "p", "work_items": [], "attention": []}


def test_bench_projects_slice_and_orders_tasks_without_host_streams():
    from fleet.projections.bench import bench_state

    summary = Summary("milestone", "Purpose", "Done", "Doing", "Next", "user", NOW)
    alert = AttentionItem("a", "p", "next", None, "blocker", "user", "work", "next",
                          "Choose", "work:next", "open", None, None, NOW)
    report = LibraryEntry("report", "p", "milestone", "r", "report", "Report", "run",
                          "file:///report", "available", True)
    doc = project([item("epic", kind="epic"), item("milestone", kind="milestone", parent="epic"),
                   item("next", parent="milestone"), item("doing", parent="milestone"),
                   item("done", parent="milestone", condition="complete")],
                  criteria=[Criterion("c", "milestone", "Approved", "accepted", None)],
                  summaries=[summary], attention=[alert], entries=[report])
    task = doc["work_items"][0]["children"][0]["children"][1]
    task["runs"] = [{"id": "r", "host": "home", "status": "running", "action_glyph": None,
                     "action_observed_at": None, "action_freshness": "unknown"}]
    result = bench_state(doc, "milestone")
    assert [(t["id"], t["lane"]) for t in result["tasks"]] == [
        ("done", "done"), ("doing", "doing"), ("next", "next")]
    assert result["criteria"][0]["verification"] == "accepted"
    assert result["agents"] == [{"run": "r", "host": "home", "status": "running", "action_glyph": None,
                                 "action_observed_at": None, "action_freshness": "unknown", "step": None}]
    assert result["attention"][0]["id"] == "a"
    assert result["reports"][0]["id"] == "report"
    assert result["summary"]["purpose"] == "Purpose"
    with pytest.raises(ValueError, match="Unknown bench"):
        bench_state(doc, "absent")


def run(identity, host, status="failed", end=NOW):
    return Run(identity, identity, host, "job", "codex", status, "lost", NOW,
               end, NOW + timedelta(minutes=1))


def test_runs_on_two_hosts_and_unavailable_trace_preserve_work():
    milestone = item("milestone", kind="milestone", condition="waiting", resume_condition="Data arrives")
    runs = [run("r1", "host-a"), run("r2", "host-b", "running", None)]
    trace = LibraryEntry("trace", "p", "milestone", "r1", "trace", "Run trace", "run",
                         "fleet://host-a/job/trace", "unavailable", True)
    result = project([milestone], runs=runs, entries=[trace, replace(trace, id="other", work_item="other")])
    node, = result["work_items"]
    assert [entry["host"] for entry in node["runs"]] == ["host-a", "host-b"]
    assert node["runs"][0] == dict(id="r1", action="r1", host="host-a", remote_job_id="job",
        runtime="codex", status="failed", reason="lost", start=NOW.isoformat(), end=NOW.isoformat(),
        last_observed=(NOW + timedelta(minutes=1)).isoformat(), usage=None, current_action=None,
        action_observed_at=None, step_work=None, kind="job", label=None, title=None, cwd=None, workspace=None,
        workspace_reason=None, action_glyph=None, action_freshness="unknown", guidance=None)
    assert node["library"][0]["availability"] == "unavailable"
    assert len(node["library"]) == 1
    assert node["condition"] == "waiting"
    assert node["progress"] == {"basis": "unknown", "complete": None, "total": None}
    assert node["attention"] == result["attention"] == []


def test_bench_uses_recorded_actions_through_execution_facade():
    from fleet.projections.bench import bench_state

    runs = [replace(run("read", "host-a", "running", None), current_action="read", action_observed_at=NOW),
            replace(run("old", "host-b", "running", None), current_action="test",
                    action_observed_at=NOW - timedelta(seconds=21)),
            run("unknown", "host-c", "running", None)]
    doc = project([item("milestone", kind="milestone")], runs=runs,
                  host_observations=[('host-a', NOW), ('host-b', NOW - timedelta(seconds=21))])
    agents = bench_state(doc, "milestone")["agents"]
    assert [(agent["action_glyph"], agent["action_freshness"]) for agent in agents] == [
        ("read", "current"), ("test", "stale"), (None, "unknown")]


@pytest.mark.parametrize("status,next_step,recorded,end,expected", [
    ("failed", None, None, NOW, True),
    ("failed", "Retry", NOW - timedelta(seconds=1), NOW, True),
    ("failed", "Retry", NOW, NOW, True),
    ("failed", "Retry", NOW + timedelta(seconds=1), NOW, False),
    ("failed", "Retry", None, NOW, None),
    ("failed", "Retry", NOW, None, None),
    ("running", None, None, None, False),
    ("unknown outcome", None, None, None, False),
    ("succeeded", None, None, NOW, False),
    ("stopped", None, None, NOW, False),
])
def test_follow_up_marker_uses_next_step_time_only(status, next_step, recorded, end, expected):
    milestone = item("milestone", next_step=next_step, next_step_recorded_at=recorded,
                     updated=NOW + timedelta(days=1))
    result = project([milestone], runs=[run("r1", "host-a", status, end)])
    node, = result["work_items"]
    assert node["no_follow_up_yet"] is expected
    assert node["attention"] == result["attention"] == []


def test_later_failure_requires_another_next_step():
    milestone = item("milestone", next_step="Retry", next_step_recorded_at=NOW + timedelta(minutes=1))
    node, = project([milestone], runs=[run("old", "host-a"),
        run("new", "host-b", end=NOW + timedelta(minutes=2))])["work_items"]
    assert node["no_follow_up_yet"] is True


def test_run_work_maps_each_job_to_its_items_ancestry():
    from fleet.projections.project import run_work

    items = [item("epic", kind="epic", title="Overhaul"), item("m", kind="milestone", parent="epic", title="3. GET URLs"),
             item("t", parent="m", title="Port")]
    work = WorkFacade(ReadWork(items), None, lambda: NOW)
    runs = [run("r1", "home"), run("r2", "home"), run("r3", "worker")]
    runs[1] = replace(runs[1], remote_job_id="relinked")
    runs.append(replace(run("r4", "home"), remote_job_id="relinked"))
    actions = {"r1": "t", "r2": "t", "r3": None, "r4": "m"}
    execution = SimpleNamespace(actions=lambda: [Action(key, value, "linked") for key, value in actions.items()],
                                runs=lambda: runs)
    links = run_work(work, execution)
    assert links["home", "job"] == {"project": "p", "chain": [
        {"id": "epic", "kind": "epic", "title": "Overhaul"}, {"id": "m", "kind": "milestone", "title": "3. GET URLs"},
        {"id": "t", "kind": "task", "title": "Port"}], "step": None, "steps": []}
    assert [node["id"] for node in links["home", "relinked"]["chain"]] == ["epic", "m"]
    assert ("worker", "job") not in links


def test_dropped_milestones_leave_progress():
    items = [item("epic", kind="epic"), item("done", parent="epic", kind="milestone", condition="complete"),
             item("gone", parent="epic", kind="milestone", condition="dropped")]
    epic, = project(items)["work_items"]
    assert epic["progress"] == {"basis": "milestones", "complete": 1, "total": 1}


def test_an_item_carries_the_relations_it_starts_with_the_other_items_title():
    items = [item("old", kind="epic", title="Old epic", condition="dropped"), item("new", kind="epic", title="New epic")]
    relations = [Relation("r1", "old", "new", "superseded-by"), Relation("r2", "old", "elsewhere", "depends-on")]
    old, new = project(items, relations=relations)["work_items"]
    assert old["relations"] == [{"type": "superseded-by", "id": "new", "title": "New epic"},
                                {"type": "depends-on", "id": "elsewhere", "title": None}]
    assert new["relations"] == []
