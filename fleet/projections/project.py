"""Persisted project work, independent of host observations."""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fleet.modules.attention import AttentionFacade
from fleet.modules.decisions import DecisionsFacade
from fleet.modules.execution import ExecutionFacade, Run
from fleet.modules.library import LibraryFacade
from fleet.modules.work import WorkFacade, WorkItem


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(entry) for entry in value]
    return value


def no_follow_up_yet(item: WorkItem, runs: list[Run]) -> bool | None:
    failed = [run for run in runs if run.status == "failed"]
    if not failed:
        return False
    if not item.next_step:
        return True
    recorded = item.next_step_recorded_at
    if recorded is None:
        return None
    if any(run.end is not None and recorded <= run.end for run in failed):
        return True
    if any(run.end is None for run in failed):
        return None
    return False


STEP_STATUS = {"pending": "pending", "done": "succeeded", "failed": "failed", "blocked": "failed",
               "cancelled": "stopped"}
IDLE_STEP = {"action_glyph": None, "action_observed_at": None, "action_freshness": "unknown"}


def step_status(run: Run, step: dict) -> str:
    """A step's state in run terms; a running step is only as running as its run (an unreachable host makes it
    an unknown outcome), and a blocked one is failed with reason blocked, as its run would be."""
    return run.status if step["status"] == "running" else STEP_STATUS[step["status"]]


def step_entries(runs: list[Run], execution: ExecutionFacade) -> dict[str, list[dict[str, Any]]]:
    """Each work item's run steps that named it, in run order, with their run's current activity; the run itself
    stays linked to its own item."""
    served: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        for step in run.step_work or []:
            served.setdefault(step["work_item"], []).append({
                "run": run.id, "host": run.host, "remote_job_id": run.remote_job_id, "step": step["index"],
                "status": step_status(run, step), "reason": "blocked" if step["status"] == "blocked" else None,
                "start": step["start"], "end": step["end"],
                # What the run is doing now belongs to the step running now, not to its earlier steps.
                **(execution.run_activity(run) if step["status"] == "running" else IDLE_STEP)})
    return served


def current_step(run: Run) -> dict | None:
    """The step of a running run that is running now and names its own work item, or None."""
    if run.status != "running":
        return None
    return next((step for step in run.step_work or [] if step["status"] == "running"), None)


def chain_of(items: dict[str, WorkItem], identity: str | None) -> list[dict[str, str]]:
    chain = []
    while identity is not None and identity in items:
        item = items[identity]
        chain.insert(0, {"id": item.id, "kind": item.kind, "title": item.title})
        identity = item.parent
    return chain


def run_work(work: WorkFacade, execution: ExecutionFacade) -> dict[tuple[str, str], dict[str, Any]]:
    """Each run's (host, remote job) mapped to its linked work item's project and root-first ancestor chain, and
    `step`: the step running now that serves its own work item, with that item's chain, or None."""
    items = {item.id: item for item in work.list()}
    actions = {action.id: action.work_item for action in execution.actions()}
    links = {}
    for run in execution.runs():  # a job linked twice keeps its latest run's item
        chain = chain_of(items, actions.get(run.action))
        step = current_step(run)
        step_chain = chain_of(items, step["work_item"]) if step is not None else []
        if chain or step_chain:
            links[run.host, run.remote_job_id] = {
                "project": items[(chain or step_chain)[-1]["id"]].project, "chain": chain,
                "step": {"index": step["index"], "chain": step_chain} if step_chain else None}
    return links


def project_status(project: str, work: WorkFacade, attention: AttentionFacade,
                   execution: ExecutionFacade, library: LibraryFacade,
                   decisions: DecisionsFacade) -> dict[str, Any]:
    items = work.list(project=project)
    raised_items = attention.list(project=project)
    open_items = attention.list(project=project, state="open")
    recorded_actions = execution.actions()
    actions = {action.id: action.work_item for action in recorded_actions}
    guidance = {action.id: action.guidance for action in recorded_actions}
    runs = execution.runs()
    served = step_entries(runs, execution)
    entries = library.list()
    answers = decisions.list()
    # Each kind of record is read once for the whole project; per-item reads made this quadratic in its items.
    criteria = work.criteria_by_item()
    summaries = work.summaries(items)
    relations = work.relations_by_item()
    titles = {item.id: item.title for item in items}
    nodes = {}
    for item in items:
        summary = summaries.get(item.id)
        own_criteria = criteria.get(item.id, [])
        item_runs = [run for run in runs if actions[run.action] == item.id]
        nodes[item.id] = {
            **asdict(item),
            "progress": asdict(work.progress_within(item, items, own_criteria)),
            "criteria": [asdict(criterion) for criterion in own_criteria],
            # The other end may be in another project; its title is then not read here.
            "relations": [{"type": relation.type, "id": relation.to_item, "title": titles.get(relation.to_item)}
                          for relation in relations.get(item.id, [])],
            "summary": asdict(summary) if summary is not None else None,
            "attention": [asdict(entry) for entry in open_items if entry.work_item == item.id],
            "decisions": [asdict(answer) for answer in answers if item.id in answer.affected_work_items],
            "runs": [{**asdict(run), **execution.run_activity(run), "guidance": guidance[run.action]}
                     for run in item_runs],
            # Run steps that named this item as the work they serve (see Run.step_work).
            "steps": served.get(item.id, []),
            "library": [asdict(entry) for entry in entries
                        if entry.project == project and entry.work_item == item.id],
            "no_follow_up_yet": no_follow_up_yet(item, item_runs),
            "children": [],
        }
    roots = []
    for item in items:
        if item.parent is None:
            roots.append(nodes[item.id])
        else:
            nodes[item.parent]["children"].append(nodes[item.id])
    def count_interruptions(node: dict[str, Any]) -> int:
        count = sum(entry.owner == "user" and entry.work_item == node["id"]
                    for entry in raised_items)
        count += sum(count_interruptions(child) for child in node["children"])
        if node["kind"] == "milestone":
            node["interruptions"] = count
        return count

    for root in roots:
        count_interruptions(root)
    return _json_value({"project": project, "work_items": roots,
                        "attention": [asdict(entry) for entry in open_items if entry.work_item is None]})
