"""Each plan line on an epic's page carries the jobs serving it: every running one and the latest other one, joined
to what their host reports (workspace, the step they are on)."""
import pytest

from fleet.composition import open_attention, open_decisions, open_execution, open_library, open_store, open_work
from fleet.projections.bench import bench_rooms
from fleet.projections.project import project_status
from fleet.web.ingester import observe_runs

WORKSPACE = {"toplevel": "/home/adi/wt/feat-x", "linked_worktree": True, "repository": "/home/adi/repo",
             "branch": "feat/x", "detached": False, "head": "abc1234", "dirty": 2, "collected_at": 1_000.0}


@pytest.fixture
def plan(project_id):
    store = open_store()
    work = open_work(store)
    epic = work.add(project=project_id, title="Epic", goal="Ship it", kind="epic", actor="user")
    milestones = [work.add(project=project_id, title=f"M{index}", goal=f"Part {index}", kind="milestone",
                           parent=epic.id, actor="user") for index in (1, 2, 3)]
    return store, project_id, epic, milestones


def dispatch(store, project, item, steps, key):
    return open_execution(store).dispatch(item.id, host="worker", runtime="claude", project=project,
        payload={"cwd": "/repo", "steps": steps}, actor="user", reason="work", idempotency_key=key).run


def job(run, statuses, items, status="running", at=1_000.0, **fields):
    """The fleetd summary of `run`'s job, started `at`, with steps in `statuses`, each serving the matching item
    (or none)."""
    steps = [{"index": index, "title": f"Part {index + 1}", "status": step_status,
              "started_at": None if step_status == "pending" else at + index * 10,
              "finished_at": at + 5 + index * 10 if step_status not in ("pending", "running") else None,
              "work_item": item}
             for index, (step_status, item) in enumerate(zip(statuses, items))]
    return {"id": run.remote_job_id, "status": status, "agent": "claude", "steps": steps, "updated_at": 1_100.0,
            "documents": [], **fields}


def observe(store, jobs):
    observe_runs(open_execution(store), open_library(store), {"name": "worker", "ok": True,
                                                              "jobs": {entry["id"]: entry for entry in jobs}})


def room(store, project, live):
    projection = project_status(project, open_work(store), open_attention(store), open_execution(store),
                                open_library(store), open_decisions(store))
    only, = bench_rooms(projection, live)["rooms"]
    return {line["title"]: line["jobs"] for line in only["plan"]}


def test_a_job_working_through_milestones_shows_on_each_line_it_served(plan):
    store, project, epic, milestones = plan
    ids = [milestone.id for milestone in milestones]
    run = dispatch(store, project, epic, [{"prompt": f"Do {index}", "work_item": identity}
                                          for index, identity in enumerate(ids)], "k")
    summary = job(run, ["done", "running", "pending"], ids, workspace=WORKSPACE, workspace_reason=None)
    observe(store, [summary])
    lines = room(store, project, {("worker", run.remote_job_id): summary})

    now, = lines["M2"]
    assert now == {"run": run.id, "host": "worker", "job": run.remote_job_id, "runtime": "claude",
                   "status": "running", "job_status": "running", "past": False, "start": now["start"], "end": None,
                   "step": {"index": 1, "count": 3, "work_item": {"id": ids[1], "title": "M2"}},
                   "workspace": WORKSPACE, "workspace_reason": None}
    # The line it served before shows the same job as done there, and where the job is now.
    before, = lines["M1"]
    assert (before["status"], before["job_status"], before["past"]) == ("succeeded", "running", True)
    assert before["step"]["work_item"]["title"] == "M2"
    # A step yet to start has served nothing.
    assert lines["M3"] == []


def test_a_line_lists_its_running_jobs_then_its_latest_finished_one(plan):
    store, project, epic, milestones = plan
    task = open_work(store).add(project=project, title="Port list", goal="Port it", parent=milestones[0].id,
                                actor="user")
    old, latest, now = (dispatch(store, project, task, [{"prompt": "Do it"}], key) for key in ("a", "b", "c"))
    observe(store, [job(old, ["done"], [None], status="done", at=1_000.0),
                    job(latest, ["failed"], [None], status="failed", at=2_000.0),
                    job(now, ["running"], [None], at=3_000.0)])
    lines = room(store, project, {})
    assert [(entry["run"], entry["status"], entry["past"]) for entry in lines["M1"]] == [
        (now.id, "running", False), (latest.id, "failed", True)]


def test_a_job_its_host_does_not_report_says_so_rather_than_guessing(plan):
    store, project, epic, milestones = plan
    ids = [milestone.id for milestone in milestones]
    run = dispatch(store, project, epic, [{"prompt": "Do 0", "work_item": ids[0]},
                                          {"prompt": "Do 1", "work_item": ids[1]}], "k")
    observe(store, [job(run, ["done", "running"], ids[:2])])
    lines = room(store, project, {})
    entry, = lines["M2"]
    assert (entry["workspace"], entry["workspace_reason"]) == (None, "the host is not reporting this job")
    # Only the running step that names its own item is known; how many steps the job has is not.
    assert entry["step"] == {"index": 1, "count": None, "work_item": {"id": ids[1], "title": "M2"}}
    assert lines["M1"][0]["step"] == entry["step"]


@pytest.mark.parametrize("fields, reason", [
    ({}, "not reported by this worker"),
    ({"workspace": None, "workspace_reason": "not a git repository"}, "not a git repository")])
def test_a_job_without_a_workspace_carries_the_reason(plan, fields, reason):
    store, project, epic, milestones = plan
    run = dispatch(store, project, milestones[0], [{"prompt": "Do it"}], "k")
    summary = job(run, ["running"], [None], **fields)
    observe(store, [summary])
    entry, = room(store, project, {("worker", run.remote_job_id): summary})["M1"]
    assert (entry["workspace"], entry["workspace_reason"]) == (None, reason)
    # A step naming no item of its own serves the job's.
    assert entry["step"] == {"index": 0, "count": 1, "work_item": {"id": milestones[0].id, "title": "M1"}}
