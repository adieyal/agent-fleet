"""Decisions recorded on a project's or an epic's work, newest first."""

from dataclasses import asdict
from typing import Any

from fleet.modules.decisions import DecisionsFacade
from fleet.modules.work import WorkFacade


def decision_log(work: WorkFacade, decisions: DecisionsFacade, *, project: str,
                 epic: str | None = None) -> list[dict[str, Any]]:
    """Decisions affecting the project's items, or the epic and its descendants; a decision affecting no
    work item is included at project level when its originating attention item identifies the project."""
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
    chosen = []
    for decision in decisions.list():
        included = bool(scope & set(decision.affected_work_items))
        if not included and epic is None and decision.attention_item:
            with decisions.repository.transaction() as transaction:
                included = transaction.attention.get(decision.attention_item).project == project
        if included:
            chosen.append(decision)
    return [{**asdict(decision), "time": decision.time.isoformat(),
             "work_items": [{"id": identity, "title": titles.get(identity)} for identity in decision.affected_work_items]}
            for decision in sorted(chosen, key=lambda decision: decision.time, reverse=True)]


def promotion_marker(decision: dict[str, Any]) -> str:
    """How a charter names a decision promoted into it."""
    return f"decision {decision['id'][:8]}"


def promotion(decision: dict[str, Any]) -> str:
    """A decision as one dated item of a charter's decisions in force."""
    flat = lambda text: " ".join(text.split())
    principle = "principle unknown" if decision["principle"] is None else f"principle: {flat(decision['principle'])}"
    return (f"{decision['time'][:10]}: {flat(decision['question'])} — {flat(decision['answer'])} "
            f"({principle}; {promotion_marker(decision)} by {decision['actor']})")
