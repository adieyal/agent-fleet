"""Bench and wayfinding documents assembled from persisted project work."""

import re
from typing import Any

UPCOMING = 3


def descendants(node: dict) -> list[dict]:
    return [node, *(child for branch in node["children"] for child in descendants(branch))]


def own_scope(epic: dict) -> list[dict]:
    """The epic and its descendants, stopping at child epics, which have their own rooms."""
    return [epic, *(item for child in epic["children"] if child["kind"] != "epic" for item in own_scope(child))]


def headline(goal: str) -> str:
    line = goal.strip().splitlines()[0]
    return re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]


def epic_room(epic: dict, parent: dict | None, depth: int) -> dict[str, Any]:
    scope = descendants(epic)
    milestones = [item for item in own_scope(epic) if item["kind"] == "milestone"]
    return {
        "id": epic["id"], "title": epic["title"], "depth": depth,
        "parent": None if parent is None else {"id": parent["id"], "title": parent["title"]},
        "goal": epic["goal"], "headline": headline(epic["goal"]),
        "milestones": {"complete": sum(item["condition"] == "complete" for item in milestones),
                       "total": len(milestones)},
        "agents": [{"run": run["id"], "host": run["host"], "work_item": item["id"], "title": item["title"]}
                   for item in scope for run in item["runs"] if run["status"] == "running"],
        "upcoming": [{"id": item["id"], "title": item["title"], "next_step": item["next_step"]}
                     for item in milestones if item["condition"] != "complete"][:UPCOMING],
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
        lane = "done" if task["condition"] == "complete" else (
            "doing" if any(run["status"] == "running" for run in task["runs"]) else "next")
        tasks.append({**task, "lane": lane})
    tasks.sort(key=lambda task: ("done", "doing", "next").index(task["lane"]))
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
