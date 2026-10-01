"""Bench and wayfinding documents assembled from persisted project work."""

import re
from typing import Any

UPCOMING = 3


def descendants(node: dict) -> list[dict]:
    return [node, *(child for branch in node["children"] for child in descendants(branch))]


def headline(goal: str) -> str:
    line = goal.strip().splitlines()[0]
    return re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]


def activity(node: dict) -> list[dict]:
    """The node's linked runs and the run steps that served it; each has a run status, start and end."""
    return node["runs"] + node["steps"]


def status(item: dict) -> str:
    """One of complete, dropped, blocked, on hold, active, ran or next; recorded condition outranks runs.

    A run step that served the item counts as a run of it, so a job working through several items lights each in
    turn. `ran` is work that is ready for review, or whose latest run finished, but which nobody has accepted as
    complete: a run never completes work."""
    condition = item["condition"]
    if condition in ("complete", "dropped", "blocked", "on hold"):
        return condition
    if condition == "waiting":
        return "on hold"
    runs = [run for node in descendants(item) for run in activity(node)]
    if any(run["status"] == "running" for run in runs):
        return "active"
    if condition == "ready for review":
        return "ran"
    latest = max(runs, key=lambda run: run.get("start") or "", default=None)
    return "ran" if latest is not None and latest["status"] == "succeeded" else "next"


def running_since(item: dict) -> str | None:
    """When the item's earliest running run started (ISO time), or None when nothing runs or its start is unknown."""
    starts = [run["start"] for node in descendants(item) for run in activity(node)
              if run["status"] == "running" and run.get("start")]
    return min(starts, default=None)


def last_run(item: dict) -> dict[str, str] | None:
    """The start and end (ISO times) of the item's latest finished run, or None when none has both recorded."""
    finished = [run for node in descendants(item) for run in activity(node)
                if run["status"] != "running" and run.get("start") and run.get("end")]
    latest = max(finished, key=lambda run: run["start"], default=None)
    return None if latest is None else {"start": latest["start"], "end": latest["end"]}


def successors(item: dict) -> list[dict]:
    return [{"id": relation["id"], "title": relation["title"]}
            for relation in item["relations"] if relation["type"] == "superseded-by"]


def line_item(item: dict) -> dict[str, Any]:
    return {"id": item["id"], "title": item["title"], "headline": headline(item["goal"]),
            "condition": item["condition"], "status": status(item), "next_step": item["next_step"],
            "plan": item["plan"], "running_since": running_since(item),
            "last_run": last_run(item), "superseded_by": successors(item), "documents": documents(item)}


READABLE = ("report", "brief", "outbox", "context")


def documents(item: dict) -> list[dict]:
    """The item's own readable documents, newest step's report first; one entry per location."""
    seen, kept = set(), []
    for entry in item["library"]:
        if entry["kind"] in READABLE and entry["canonical_location"] not in seen:
            seen.add(entry["canonical_location"])
            kept.append({key: entry[key] for key in ("kind", "title", "canonical_location", "availability")})
    order = sorted(enumerate(kept), key=lambda pair: (READABLE.index(pair[1]["kind"]), -pair[0]))
    return [entry for _, entry in order]


def milestone_groups(epic: dict) -> tuple[list[dict], list[tuple[dict, list[dict]]]]:
    """The epic's milestones outside any workstream, then each workstream with its own; child epics have their own rooms."""
    direct: list[dict] = []
    streams: list[tuple[dict, list[dict]]] = []

    def visit(node: dict, bucket: list[dict]) -> None:
        for child in node["children"]:
            if child["kind"] == "epic":
                continue
            if child["kind"] == "workstream":
                streams.append((child, own := []))
                visit(child, own)
                continue
            if child["kind"] == "milestone":
                bucket.append(child)
            visit(child, bucket)

    visit(epic, direct)
    return direct, streams


def milestone_count(milestones: list[dict]) -> dict[str, int]:
    """Dropped milestones are neither done nor still to do, so they leave the count."""
    kept = [item for item in milestones if item["condition"] != "dropped"]
    return {"complete": sum(item["condition"] == "complete" for item in kept), "total": len(kept)}


def outstanding(item: dict) -> bool:
    return item["condition"] not in ("complete", "dropped")


def upcoming(item: dict) -> dict[str, Any]:
    return {"id": item["id"], "title": item["title"], "next_step": item["next_step"]}


def workstream(stream: dict, milestones: list[dict]) -> dict[str, Any]:
    pending = [item for item in milestones if outstanding(item)]
    return {"id": stream["id"], "title": stream["title"], "milestones": milestone_count(milestones),
            "next": upcoming(pending[0]) if pending else None,
            "plan": [line_item(item) for item in milestones]}


def agents(scope: list[dict]) -> list[dict[str, Any]]:
    """Each running run in scope once: at the item its running step serves, else at the item it is linked to.

    `step` is that step's index, or None for a run seen through its link alone."""
    placed: dict[str, dict[str, Any]] = {}
    for node in scope:
        for run in node["runs"]:
            if run["status"] == "running":
                placed[run["id"]] = {"run": run["id"], "host": run["host"], "work_item": node["id"],
                                     "title": node["title"], "step": None}
    for node in scope:
        for entry in node["steps"]:
            if entry["status"] == "running":
                placed[entry["run"]] = {"run": entry["run"], "host": entry["host"], "work_item": node["id"],
                                        "title": node["title"], "step": entry["step"]}
    return list(placed.values())


def epic_room(epic: dict, parent: dict | None, depth: int) -> dict[str, Any]:
    scope = descendants(epic)
    direct, streams = milestone_groups(epic)
    milestones = direct + [item for _, own in streams for item in own]
    return {
        "id": epic["id"], "title": epic["title"], "depth": depth,
        "condition": epic["condition"], "superseded_by": successors(epic),
        "parent": None if parent is None else {"id": parent["id"], "title": parent["title"]},
        "goal": epic["goal"], "headline": headline(epic["goal"]),
        "criteria": epic["criteria"], "progress": epic["progress"],
        "plan": [line_item(item) for item in direct],
        "workstreams": [workstream(stream, own) for stream, own in streams],
        "tasks": [line_item(child) for child in epic["children"] if child["kind"] == "task"],
        "milestones": milestone_count(milestones),
        "agents": agents(scope),
        "upcoming": [upcoming(item) for item in milestones if outstanding(item)][:UPCOMING],
        "children": [{"id": child["id"], "title": child["title"]}
                     for child in epic["children"] if child["kind"] == "epic"],
        "attention": [{"id": entry["id"], "kind": entry["kind"], "headline": entry["headline"],
                       "work_item": item["id"]}
                      for item in scope for entry in item["attention"]
                      if entry["kind"] in ("decision", "blocker")],
        "benches": [{"id": item["id"], "title": item["title"]} for item in scope if item["kind"] == "milestone"],
    }


def bench_rooms(project: dict) -> dict:
    rooms = []

    def visit(node: dict, parent: dict | None, depth: int) -> None:
        if node["kind"] == "epic":
            rooms.append(epic_room(node, parent, depth))
            parent, depth = node, depth + 1
        for child in node["children"]:
            visit(child, parent, depth)

    for root in project["work_items"]:
        visit(root, None, 0)
    return {"project": project["project"], "rooms": rooms}


def bench_state(project: dict, identity: str) -> dict[str, Any]:
    nodes = [node for root in project["work_items"] for node in descendants(root)]
    node = next((node for node in nodes if node["id"] == identity and node["kind"] == "milestone"), None)
    if node is None:
        raise ValueError(f"Unknown bench: {identity}")
    scope = descendants(node)
    tasks = []
    for task in scope:
        if task["kind"] != "task":
            continue
        lane = "done" if task["condition"] == "complete" else "dropped" if task["condition"] == "dropped" else (
            "doing" if any(run["status"] == "running" for run in activity(task)) else "next")
        tasks.append({**task, "lane": lane})
    tasks.sort(key=lambda task: ("done", "doing", "next", "dropped").index(task["lane"]))
    linked = [run for item in scope for run in item["runs"]]
    # A run linked elsewhere appears once through the steps it ran here: the running one, else the last listed.
    known = {run["id"] for run in linked}
    stepped: dict[str, dict] = {}
    for entry in (entry for item in scope for entry in item["steps"] if entry["run"] not in known):
        if stepped.get(entry["run"], {}).get("status") != "running":
            stepped[entry["run"]] = entry
    return {"id": identity, "project": project["project"], "title": node["title"],
            "tasks": tasks, "criteria": node["criteria"], "progress": node["progress"],
            "summary": node["summary"],
            "agents": [{"run": run["id"], "host": run["host"], "status": run["status"],
                        "action_glyph": run["action_glyph"],
                        "action_observed_at": run["action_observed_at"],
                        "action_freshness": run["action_freshness"], "step": None}
                       for run in linked] + [
                      {"run": entry["run"], "host": entry["host"], "status": entry["status"],
                       "action_glyph": entry["action_glyph"], "action_observed_at": entry["action_observed_at"],
                       "action_freshness": entry["action_freshness"], "step": entry["step"]}
                      for entry in stepped.values()],
            "attention": [entry for item in scope for entry in item["attention"]],
            "reports": [entry for item in scope for entry in item["library"] if entry["kind"] == "report"]}
