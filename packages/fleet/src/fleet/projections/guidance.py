"""The room's guidance: raw constitution or charter data, its versions, and an epic's decisions."""

from dataclasses import asdict
from typing import Any
from fleet.modules.work import WorkFacade
from fleet.modules.records import RecordsFacade
from fleet.modules.decisions import DecisionsFacade

from fleet.projections.decisions import decision_log, promotion_marker


def guidance_view(work: WorkFacade, records: RecordsFacade, project: str, epic: str | None = None, number: int | None = None) -> dict[str, Any]:
    """The current (or numbered) version as raw reader data, with its history; "guidance" is None when
    nothing is recorded; saving the first version creates the project's management repository."""
    title = "Constitution" if epic is None else f"Charter: {work.get(epic).title}"
    view = {"project": project, "epic": epic, "name": title, "triage_policy": records.triage_policy(project)}
    current = records.guidance(project, epic, number=number)
    history = [asdict(version) for version in records.guidance_history(project, epic)]
    if current is None:
        return {**view, "guidance": None, "history": history}
    described = {key: value for key, value in asdict(current).items() if key != "body"}
    return {**view, "markdown": current.body, "name": f"{title} · version {current.version.number}",
            "guidance": described, "history": history}


def epic_decisions(work: WorkFacade, records: RecordsFacade, decisions: DecisionsFacade, epic: str) -> dict[str, Any]:
    """Decisions on the epic's work, newest first; "promoted" says whether the current charter already holds one,
    and is None when no charter is recorded to promote into."""
    project = work.get(epic).project
    charter = records.guidance(project, epic)
    return {"charter": charter is not None,
            "decisions": [{**entry, "promoted": None if charter is None else promotion_marker(entry) in charter.body}
                          for entry in decision_log(work, decisions, project=project, epic=epic)]}


def project_decisions(work: WorkFacade, decisions: DecisionsFacade, project: str) -> dict[str, Any]:
    return {"project": project, "decisions": decision_log(work, decisions, project=project)}
