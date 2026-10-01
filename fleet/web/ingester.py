"""Translate fleetd observations into module commands."""

from datetime import datetime, timezone
from urllib.parse import quote

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
                 indexed: dict | None = None) -> None:
    """Record the host's jobs as runs, and index each linked run's documents in the library.

    `indexed`, kept by the caller across calls, remembers each entry as last indexed so an unchanged one is not
    written again: hosts report many times a minute, and every job's documents come with every report."""
    execution.observe_host(host['name'], reachable=host['ok'])
    if not host["ok"]:
        execution.unavailable(host["name"])
        return
    actions = {action.id: action for action in execution.actions()}
    for job in host["jobs"].values():
        if execution.find_run(host["name"], job["id"]) is None:
            continue
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
