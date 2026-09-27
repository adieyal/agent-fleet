"""Bench and wayfinding documents assembled from persisted project work."""

from typing import Any


def descendants(node: dict) -> list[dict]:
    return [node, *(child for branch in node["children"] for child in descendants(branch))]


def bench_rooms(project: dict) -> dict:
    nodes = [node for root in project["work_items"] for node in descendants(root)]
    return {"project": project["project"], "rooms": [
        {"id": node["id"], "title": node["title"], "benches": [
            {"id": child["id"], "title": child["title"]}
            for child in descendants(node) if child["kind"] == "milestone"]}
        for node in nodes if node["kind"] == "epic"]}


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
                        "action_glyph": None} for item in scope for run in item["runs"]],
            "attention": [entry for item in scope for entry in item["attention"]],
            "reports": [entry for item in scope for entry in item["library"] if entry["kind"] == "report"]}
