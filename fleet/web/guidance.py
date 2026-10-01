"""The room's guidance: a constitution or charter rendered for the deck, its versions, and an epic's decisions."""

from dataclasses import asdict
from typing import Any

from fleet.projections.decisions import decision_log, promotion_marker
from fleet.web.documents import render_markdown


def guidance_view(services, project: str, epic: str | None = None, number: int | None = None) -> dict[str, Any]:
    """The current (or numbered) version rendered as a reader document, with its history; "guidance" is None when
    nothing is recorded, and "registered" is False when the project has no management repository to record it in."""
    title = "Constitution" if epic is None else f"Charter: {services.work.get(epic).title}"
    view = {"project": project, "epic": epic, "name": title, "registered": services.records.registered(project)}
    if not view["registered"]:
        return {**view, "guidance": None, "history": [],
                "register": f"fleet project management {project} <path>"}
    current = services.records.guidance(project, epic, number=number)
    history = [asdict(version) for version in services.records.guidance_history(project, epic)]
    if current is None:
        return {**view, "guidance": None, "history": history}
    described = {key: value for key, value in asdict(current).items() if key != "body"}
    return {**view, **render_markdown(current.body), "name": f"{title} · version {current.version.number}",
            "guidance": described, "history": history}


def epic_decisions(services, epic: str) -> dict[str, Any]:
    """Decisions on the epic's work, newest first; "promoted" says whether the current charter already holds one,
    and is None when no charter is recorded to promote into."""
    project = services.work.get(epic).project
    charter = services.records.guidance(project, epic) if services.records.registered(project) else None
    return {"charter": charter is not None,
            "decisions": [{**entry, "promoted": None if charter is None else promotion_marker(entry) in charter.body}
                          for entry in decision_log(services.work, services.decisions, project=project, epic=epic)]}
