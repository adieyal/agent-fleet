"""Store-backed run history shared by the CLI and HTTP reader."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import re

from fleet.identifiers import resolve_prefix
from fleet.modules.execution import ExecutionFacade, Run
from fleet.modules.library import LibraryFacade
from fleet.modules.work import WorkFacade
from fleet.modules.workspace import WorkspaceFacade


STATUSES = {"running", "succeeded", "failed", "stopped", "unknown outcome"}


def cutoff(value: str | None, now: datetime, *, relative: bool = False) -> datetime | None:
    if value is None:
        return None
    if relative and (match := re.fullmatch(r"(\d+)([dhm])", value)):
        return now - timedelta(seconds=int(match[1]) * {"d": 86400, "h": 3600, "m": 60}[match[2]])
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid date: {value}; use an ISO date or time") from error
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def record(run: Run, execution: ExecutionFacade, work: WorkFacade) -> dict:
    action = execution.get_action(run.action)
    value = asdict(run)
    value['status_label'] = ({'queued': 'queued (not started)', 'stalled': 'stalled (outcome unknown)'}.get(run.reason, run.status)
                             if run.status == 'unknown outcome' else run.status)
    for key in ("start", "end", "last_observed", "action_observed_at"):
        value[key] = value[key].isoformat() if value[key] is not None else None
    value["project"] = work.get(action.work_item).project if action.work_item else action.project
    value["work_item"] = action.work_item
    value["action_source"] = action.source
    value["model"] = (action.payload or {}).get("model")
    value["work_title"] = work.get(action.work_item).title if action.work_item else None
    host = next((entry for entry in execution.hosts() if entry["name"] == run.host), None)
    value["offline_since"] = host["since"] if host and not host["reachable"] else None
    value["duration_seconds"] = (run.end - run.start).total_seconds() if run.start and run.end else None
    recorded_git = [step["git"] for step in execution.steps(run.id) if "commit_count" in step["git"]]
    value["commit_count"] = sum(git["commit_count"] for git in recorded_git) if recorded_git else None
    value["push_count"] = sum(len(git.get("pushes", [])) for git in recorded_git) if recorded_git else None
    return value


def history_runs(execution: ExecutionFacade, work: WorkFacade, workspace: WorkspaceFacade, *,
                 project: str | None = None, work_item: str | None = None, descendants: bool = False,
                 host: str | None = None, status: str | None = None, kind: str | None = None,
                 unlinked: bool = False, since: str | None = None, until: str | None = None,
                 limit: int = 50, now: datetime | None = None) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    for name, value in (("project", project), ("work_item", work_item), ("host", host), ("status", status), ("kind", kind)):
        if value is not None and not value.strip():
            raise ValueError(f"{name} must not be empty")
    if kind is not None and kind not in ("job", "session"):
        raise ValueError("kind must be job or session")
    statuses = set(status.split(",")) if status else None
    if statuses is not None and statuses - STATUSES:
        raise ValueError(f"unknown run status: {', '.join(sorted(statuses - STATUSES))}")
    if descendants and not work_item:
        raise ValueError("descendants requires work_item")
    project_id = workspace.resolve_project(project) if project is not None else None
    selected = None
    if work_item:
        work_item = resolve_prefix(work_item, [item.id for item in work.list()], 'work item')
        selected = {work_item}
        if descendants:
            items = work.list()
            while added := {item.id for item in items if item.parent in selected} - selected:
                selected.update(added)
    clock = now or datetime.now(timezone.utc)
    lower, upper = cutoff(since, clock, relative=True), cutoff(until, clock)
    if lower and upper and lower > upper:
        raise ValueError("since must be before or equal to until")
    actions = {action.id: action for action in execution.actions()}
    found = []
    for run in execution.runs():
        action = actions[run.action]
        run_project = work.get(action.work_item).project if action.work_item else action.project
        if project_id is not None and run_project != project_id:
            continue
        served = {action.work_item} | {step["work_item"] for step in run.step_work or []}
        if selected is not None and not served.intersection(selected):
            continue
        if host is not None and run.host != host or statuses is not None and run.status not in statuses:
            continue
        if kind is not None and run.kind != kind or unlinked and action.work_item is not None:
            continue
        if lower and (run.start is None or run.start < lower) or upper and (run.start is None or run.start > upper):
            continue
        found.append(run)
    found.sort(key=lambda run: (run.start or datetime.min.replace(tzinfo=timezone.utc), run.id), reverse=True)
    return {"runs": [record(run, execution, work) for run in found[:limit]], "total": len(found),
            "limit": limit, "empty_reason": "No stored runs match these filters." if not found else None}


def run_detail(identity: str, execution: ExecutionFacade, work: WorkFacade, library: LibraryFacade) -> dict:
    if not identity:
        raise ValueError("a run ID or prefix is required")
    matches = [run for run in execution.runs() if run.id.startswith(identity)]
    if not matches:
        raise LookupError(f"no stored run matches {identity}")
    exact = next((run for run in matches if run.id == identity), None)
    if exact is None and len(matches) != 1:
        raise ValueError(f"run prefix {identity} matches {len(matches)} runs; use more of the id")
    run = exact or matches[0]
    return {"run": record(run, execution, work), "action": asdict(execution.get_action(run.action)),
            "steps": execution.steps(run.id), "documents": [asdict(entry) for entry in library.list()
                if entry.run == run.id and entry.kind != "trace"],
            "trace": execution.trace(run.id)}


def kept_run_detail(identity, execution, work, library, documents):
    detail = run_detail(identity, execution, work, library)
    detail['kept_documents'] = documents.run_documents(detail['run'])
    return detail
