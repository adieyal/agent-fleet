"""A job's steps may each serve their own work item: the bench lights each while its step runs, and the run stays
linked to the job's item, unable to complete any of them."""
import pytest

from fleet.composition import open_execution, open_library, open_store, open_work, open_attention, open_decisions
from fleet.projections.bench import bench_rooms
from fleet.projections.project import project_status, run_work
from fleet.web.ingester import observe_runs


@pytest.fixture
def plan(project_id):
    store = open_store()
    work = open_work(store)
    epic = work.add(project=project_id, title="Epic", goal="Ship it", kind="epic", actor="user")
    milestones = [work.add(project=project_id, title=f"M{index}", goal=f"Part {index}", kind="milestone",
                           parent=epic.id, actor="user") for index in (1, 2, 3)]
    return store, project_id, epic, milestones


def dispatch(store, project, epic, milestones, key="k"):
    steps = [{"prompt": f"Do part {index}", "work_item": milestone.id}
             for index, milestone in enumerate(milestones, 1)]
    return open_execution(store).dispatch(epic.id, host="worker", runtime="claude", project=project,
        payload={"cwd": "/repo", "steps": steps}, actor="user", reason="three parts", idempotency_key=key)


def observe(store, run, statuses, job_status="running"):
    clock = 1_000.0
    steps = []
    for index, (milestone_status, work_item) in enumerate(statuses):
        started = clock + index * 10 if milestone_status != "pending" else None
        finished = started + 5 if milestone_status not in ("pending", "running") else None
        steps.append({"index": index, "title": f"Part {index + 1}", "status": milestone_status,
                      "started_at": started, "finished_at": finished, "work_item": work_item})
    job = {"id": run.remote_job_id, "status": job_status, "agent": "claude", "steps": steps, "updated_at": clock + 100,
           "documents": []}
    observe_runs(open_execution(store), open_library(store), {"name": "worker", "ok": True, "jobs": {job["id"]: job}})


def plan_status(store, project):
    projection = project_status(project, open_work(store), open_attention(store), open_execution(store),
                                open_library(store), open_decisions(store))
    room, = bench_rooms(projection, {})["rooms"]
    return room, [line["status"] for line in room["plan"]]


def test_a_three_step_job_over_three_milestones_lights_each_in_turn(plan):
    store, project, epic, milestones = plan
    run = dispatch(store, project, epic, milestones).run
    ids = [milestone.id for milestone in milestones]
    phases = [
        (["running", "pending", "pending"], ["active", "next", "next"]),
        (["done", "running", "pending"], ["ran", "active", "next"]),
        (["done", "done", "running"], ["ran", "ran", "active"]),
    ]
    for step_statuses, expected in phases:
        observe(store, run, list(zip(step_statuses, ids)))
        room, statuses = plan_status(store, project)
        assert statuses == expected
        current = step_statuses.index("running")
        assert room["agents"] == [{"run": run.id, "host": "worker", "work_item": ids[current],
                                   "title": f"M{current + 1}", "step": current}]
        assert run_work(open_work(store), open_execution(store))["worker", run.remote_job_id]["step"] == {
            "index": current, "chain": [{"id": epic.id, "kind": "epic", "title": "Epic"},
                                        {"id": ids[current], "kind": "milestone", "title": f"M{current + 1}"}]}
    observe(store, run, list(zip(["done"] * 3, ids)), job_status="done")
    room, statuses = plan_status(store, project)
    assert statuses == ["ran", "ran", "ran"]
    assert room["agents"] == []
    # A run finishing completes nothing: every milestone keeps its recorded condition.
    assert [open_work(store).get(identity).condition for identity in ids] == [m.condition for m in milestones]
    assert open_execution(store).get_run(run.id).status == "succeeded"


def test_an_unreachable_host_leaves_its_running_step_unknown_not_active(plan):
    store, project, epic, milestones = plan
    run = dispatch(store, project, epic, milestones).run
    observe(store, run, [("running", milestones[0].id), ("pending", milestones[1].id), ("pending", milestones[2].id)])
    open_execution(store).unavailable("worker")
    _, statuses = plan_status(store, project)
    assert statuses == ["next", "next", "next"]


def test_step_work_is_part_of_the_dispatch_and_its_idempotency(plan):
    store, project, epic, milestones = plan
    first = dispatch(store, project, epic, milestones)
    again = dispatch(store, project, epic, milestones)
    assert (again.created, again.run.id) == (False, first.run.id)
    with pytest.raises(ValueError, match="different payload"):
        dispatch(store, project, epic, list(reversed(milestones)))


def test_a_step_may_only_serve_existing_work_in_the_jobs_project(plan):
    store, project, epic, milestones = plan
    elsewhere = open_work(store).add(project="another", title="Other", goal="Elsewhere", actor="user")
    with pytest.raises(ValueError, match="another project"):
        dispatch(store, project, epic, [elsewhere], key="other")
    with pytest.raises(LookupError):
        dispatch(store, project, epic, [type("Missing", (), {"id": "w-missing"})()], key="missing")


def test_a_steps_documents_belong_to_the_work_it_served(plan):
    store, project, epic, milestones = plan
    run = dispatch(store, project, epic, milestones).run
    job = {"id": run.remote_job_id, "status": "running", "agent": "claude", "updated_at": 1.0,
           "steps": [{"index": 0, "status": "done", "started_at": 1.0, "finished_at": 2.0,
                      "work_item": milestones[0].id},
                     {"index": 1, "status": "running", "started_at": 3.0, "finished_at": None}],
           "documents": [{"kind": "report", "name": "Step 1", "path": "/jobs/j/result-0.md", "step": 0},
                         {"kind": "report", "name": "Step 2", "path": "/jobs/j/result-1.md", "step": 1},
                         {"kind": "outbox", "name": "REPORT.md", "path": "/jobs/j/outbox/REPORT.md", "step": None}]}
    observe_runs(open_execution(store), open_library(store), {"name": "worker", "ok": True, "jobs": {job["id"]: job}})
    placed = {entry.title: entry.work_item for entry in open_library(store).list()}
    assert placed == {"Step 1": milestones[0].id, "Step 2": epic.id, "REPORT.md": epic.id}


def test_the_deck_state_carries_each_jobs_workspace_and_current_step_work(plan):
    from fleet.transport import Host
    from fleet.web import server

    store, project, epic, milestones = plan
    run = dispatch(store, project, epic, milestones).run
    workspace = {"toplevel": "/repo-wt", "linked_worktree": True, "repository": "/repo", "branch": "feat/x",
                 "detached": False, "head": "abc1234", "dirty": 0, "collected_at": 5.0}
    host = Host("worker", None)
    state = server.FleetState([host], store=store)
    server.apply_message(state, host, {"type": "hello"})
    server.apply_message(state, host, {"type": "job", "job": {
        "id": run.remote_job_id, "project": "p", "description": "three parts", "status": "running",
        "agent": "claude", "created_at": 1, "updated_at": 2, "documents": [], "cwd": "/repo-wt",
        "workspace": workspace, "workspace_reason": None,
        "steps": [{"index": 0, "title": "Part 1", "status": "done", "started_at": 1.0, "finished_at": 2.0,
                   "work_item": milestones[0].id},
                  {"index": 1, "title": "Part 2", "status": "running", "started_at": 3.0, "finished_at": None,
                   "work_item": milestones[1].id}]}})
    job, = state.document()["hosts"][0]["jobs"]
    assert (job["workspace"], job["workspace_reason"]) == (workspace, None)
    assert [node["id"] for node in job["work"]["chain"]] == [epic.id]
    assert job["work"]["step"]["index"] == 1
    assert [node["id"] for node in job["work"]["step"]["chain"]] == [epic.id, milestones[1].id]
    assert [(step["index"], step["chain"][-1]["id"]) for step in job["work"]["steps"]] == [
        (0, milestones[0].id), (1, milestones[1].id)]
