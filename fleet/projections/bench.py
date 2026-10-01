"""Bench and wayfinding documents assembled from persisted project work."""

import re
from typing import Any

UPCOMING = 3


def descendants(node: dict) -> list[dict]:
    return [node, *(child for branch in node["children"] for child in descendants(branch))]


def headline(goal: str) -> str:
    line = goal.strip().splitlines()[0]
    return re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]


def status(item: dict) -> str:
    """One of complete, dropped, blocked, on hold, active, ran or next; recorded condition outranks runs.

    `ran` is work that is ready for review, or whose latest run finished, but which nobody has accepted as complete:
    a run never completes work."""
    condition = item["condition"]
    if condition in ("complete", "dropped", "blocked", "on hold"):
        return condition
    if condition == "waiting":
        return "on hold"
    runs = [run for node in descendants(item) for run in node["runs"]]
    if any(run["status"] == "running" for run in runs):
        return "active"
    if condition == "ready for review":
        return "ran"
    latest = max(runs, key=lambda run: run.get("start") or "", default=None)
    return "ran" if latest is not None and latest["status"] == "succeeded" else "next"


def running_since(item: dict) -> str | None:
    """When the item's earliest running run started (ISO time), or None when nothing runs or its start is unknown."""
    starts = [run["start"] for node in descendants(item) for run in node["runs"]
              if run["status"] == "running" and run.get("start")]
    return min(starts, default=None)


def last_run(item: dict) -> dict[str, str] | None:
    """The start and end (ISO times) of the item's latest finished run, or None when none has both recorded."""
    finished = [run for node in descendants(item) for run in node["runs"]
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
            "last_run": last_run(item), "superseded_by": successors(item)}


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
        "agents": [{"run": run["id"], "host": run["host"], "work_item": item["id"], "title": item["title"]}
                   for item in scope for run in item["runs"] if run["status"] == "running"],
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
            "doing" if any(run["status"] == "running" for run in task["runs"]) else "next")
        tasks.append({**task, "lane": lane})
    tasks.sort(key=lambda task: ("done", "doing", "next", "dropped").index(task["lane"]))
    return {"id": identity, "project": project["project"], "title": node["title"],
            "tasks": tasks, "criteria": node["criteria"], "progress": node["progress"],
            "summary": node["summary"],
            "agents": [{"run": run["id"], "host": run["host"], "status": run["status"],
                        "action_glyph": run["action_glyph"],
                        "action_observed_at": run["action_observed_at"],
                        "action_freshness": run["action_freshness"]}
                       for item in scope for run in item["runs"]],
            "attention": [entry for item in scope for entry in item["attention"]],
            "reports": [entry for item in scope for entry in item["library"] if entry["kind"] == "report"]}
