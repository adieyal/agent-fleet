"""Decisions recorded on a project's or an epic's work, newest first."""

from dataclasses import asdict
from typing import Any

from fleet.modules.decisions import DecisionsFacade
from fleet.modules.work import WorkFacade


def decision_log(work: WorkFacade, decisions: DecisionsFacade, *, project: str,
                 epic: str | None = None) -> list[dict[str, Any]]:
    """Decisions affecting the project's items, or the epic and its descendants; a decision affecting no
    work item has no project and is not listed."""
    items = work.list(project=project)
    if epic is None:
        scope = {item.id for item in items}
    else:
        children: dict[str, list[str]] = {}
        for item in items:
            children.setdefault(item.parent, []).append(item.id)
        scope, frontier = set(), [epic]
        while frontier:
            identity = frontier.pop()
            scope.add(identity)
            frontier += children.get(identity, [])
    titles = {item.id: item.title for item in items}
    chosen = [decision for decision in decisions.list() if scope & set(decision.affected_work_items)]
    return [{**asdict(decision), "time": decision.time.isoformat(),
             "work_items": [{"id": identity, "title": titles.get(identity)} for identity in decision.affected_work_items]}
            for decision in sorted(chosen, key=lambda decision: decision.time, reverse=True)]
