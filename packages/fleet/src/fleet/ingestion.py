"""Translate fleetd observations into module commands."""

import sys
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

from fleet.modules.attention import AttentionFacade
from fleet.modules.decisions import DecisionsFacade
from fleet.modules.execution import ExecutionFacade, JobObservation, Usage
from fleet.modules.library import LibraryFacade


def timestamp(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, timezone.utc) if value is not None else None


def iso(value: float | None) -> str | None:
    return timestamp(value).isoformat() if value is not None else None


def step_work(job: dict) -> list[dict] | None:
    """The job's steps that name their own work item (see Run.step_work), or None when none does."""
    named = [{"index": step["index"], "work_item": step["work_item"], "status": step["status"],
              "start": iso(step["started_at"]), "end": iso(step["finished_at"])}
             for step in job["steps"] if step.get("work_item")]
    return named or None


def observe_runs(execution: ExecutionFacade, library: LibraryFacade, host: dict,
                 indexed: dict | None = None, project_of: Callable[[dict], str | None] | None = None,
                 *, observed: dict | None = None) -> None:
    """Record the host's jobs as runs, and index each linked run's documents in the library.

    `indexed`, kept by the caller across calls, remembers each entry as last indexed so an unchanged one is not
    written again: hosts report many times a minute, and every job's documents come with every report.

    `observed` caches successful payloads plus resolved project and persisted run/action state.
    Reading current state once per report preserves later linking and external run changes.
    Cache entries are copied because worker reports and facade values contain mutable dictionaries.
    """
    execution.observe_host(host['name'], reachable=host['ok'])
    if not host["ok"]:
        execution.unavailable(host["name"])
        return
    actions = {action.id: action for action in execution.actions()}
    runs = {(run.host, run.remote_job_id): run for run in execution.runs()} if observed is not None else {}
    if observed is not None:
        for key in list(observed):
            if key[0] == host["name"] and key[1] not in host["jobs"]:
                del observed[key]
    for job in host["jobs"].values():
        if job.get("stale"):
            continue
        project = project_of(job) if project_of else None
        key = (host["name"], job["id"])
        existing = runs.get(key)
        context = (job, project, existing, actions.get(existing.action) if existing else None)
        if observed is not None and observed.get(key) == context:
            continue
        run = execution.record_observed(host["name"], job, project)
        actions[run.action] = execution.get_action(run.action)
        starts = [step["started_at"] for step in job["steps"] if step["started_at"] is not None]
        ends = [step["finished_at"] for step in job["steps"] if step["finished_at"] is not None]
        end = max(ends) if ends and job["status"] in ("done", "failed", "blocked", "cancelled") else None
        event = job.get("activity")
        observation = JobObservation(job["id"], job["status"], job.get("agent"),
                                     timestamp(min(starts)) if starts else None, timestamp(end),
                                     timestamp(job.get("updated_at")), Usage.from_worker(job),
                                     execution.classify_activity(event),
                                     timestamp(event.get("ts")) if event is not None else None,
                                     step_work(job))
        run = execution.observe(host["name"], observation)
        if run is None:
            continue
        execution.observe_steps(run.id, [{"index": step["index"], "title": step.get("title"),
            "status": step["status"], "start": iso(step["started_at"]), "end": iso(step["finished_at"]),
            "work_item": step.get("work_item"),
            "git": step.get("git", {"reason": "not recorded: the worker did not report per-step git"})}
            for step in job["steps"]])
        # A step's own documents belong to the work it served; the rest to the job's work item.
        served = {step["index"]: step["work_item"] for step in run.step_work or []}
        outputs = [(document["kind"], document["name"], document["path"], "available", document.get("step"))
                   for document in job["documents"]] if "documents" in job else []
        if "trace" in job:
            trace = job["trace"]
            outputs.append(("trace", "Run trace", trace["path"], trace["availability"], None))
        for kind, title, path, availability, step in outputs:
            work_item = served.get(step, actions[run.action].work_item)
            if work_item is None:
                continue
            location = f"fleet://{quote(host['name'], safe='')}{quote(path, safe='/')}"
            if indexed is not None and indexed.get((run.id, kind, location)) == (work_item, title, availability):
                continue
            library.index_run(run=run.id, work_item=work_item, kind=kind,
                              title=title, location=location, availability=availability)
            if indexed is not None:
                indexed[run.id, kind, location] = (work_item, title, availability)
        if observed is not None:
            observed[key] = deepcopy((job, project, run, actions[run.action]))


def observe_sessions(execution: ExecutionFacade, host: dict, project_of: Callable[[dict], str | None]) -> None:
    if not host["ok"]:
        return
    for session in host["sessions"].values():
        if not session.get("stale"):
            execution.observe_session(host["name"], session, project_of(session))


def record_decisions(decisions: DecisionsFacade, execution: ExecutionFacade, attention: AttentionFacade,
                     host: dict, project_of: Callable[[dict], str | None], taken: set | None = None) -> None:
    """Record the decisions agents held on the host's jobs (fleetd `decision`), once per id, linked to the job's run.

    A decision whose job has no run is recorded without one. One that cannot be recorded (an unknown work item, a
    run in another project) becomes an alert in its job's project rather than being dropped. `taken`, kept by the
    caller across calls, remembers the ids already handled so a host's frequent reports do not touch the store.
    """
    if not host["ok"]:
        return
    for job in host["jobs"].values():
        for held in job.get("decisions", []):
            if taken is not None and held["id"] in taken:
                continue
            run = execution.find_run(host["name"], job["id"])
            try:
                decisions.record_streamed(held["id"], timestamp(held["time"]), held["work_item"],
                    actor=held["actor"], question=held["question"], answer=held["answer"],
                    principle=held["principle"], context=held["context"],
                    source_run=None if run is None else run.id)
            except (ValueError, LookupError) as error:
                project = execution.get_action(run.action).project if run is not None else project_of(job)
                if project is None:
                    print(f"fleet: decision {held['id']} on {host['name']} job {job['id']} not recorded, and the "
                          f"job has no project to raise it in: {error}", file=sys.stderr)
                else:
                    attention.raise_item(project=project, kind="alert", owner="user", source="decision-stream",
                        source_reference=held["id"], headline=f"Agent's decision not recorded: {error}",
                        context_reference=f"job:{host['name']}:{job['id']}", actor="host-stream",
                        run=None if run is None else run.id)
            if taken is not None:
                taken.add(held["id"])
