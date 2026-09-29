"""Translate fleetd observations into module commands."""

from datetime import datetime, timezone
from urllib.parse import quote

from fleet.modules.execution import ExecutionFacade, JobObservation, Usage
from fleet.modules.library import LibraryFacade


def timestamp(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, timezone.utc) if value is not None else None


def observe_runs(execution: ExecutionFacade, library: LibraryFacade, host: dict) -> None:
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
                                     timestamp(event.get("ts")) if event is not None else None)
        run = execution.observe(host["name"], observation)
        if run is None or actions[run.action].work_item is None:
            continue
        outputs = [(document["kind"], document["name"], document["path"], "available")
                   for document in job["documents"]] if "documents" in job else []
        if "trace" in job:
            trace = job["trace"]
            outputs.append(("trace", "Run trace", trace["path"], trace["availability"]))
        for kind, title, path, availability in outputs:
            location = f"fleet://{quote(host['name'], safe='')}{quote(path, safe='/')}"
            library.index_run(run=run.id, work_item=actions[run.action].work_item, kind=kind,
                              title=title, location=location, availability=availability)
