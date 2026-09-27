"""Work records and acceptance rules."""

from dataclasses import dataclass, replace
from datetime import datetime

KINDS = ("epic", "workstream", "milestone", "task")
CONDITIONS = ("none", "waiting", "ready for review", "blocked", "on hold", "complete")


@dataclass(frozen=True)
class Progress:
    basis: str
    complete: int | None
    total: int | None


def accepted_progress(children: list["WorkItem"], criteria: list["Criterion"]) -> Progress:
    milestones = [item for item in children if item.kind == "milestone"]
    if milestones:
        return Progress("milestones", sum(item.condition == "complete" for item in milestones), len(milestones))
    if criteria:
        return Progress("criteria", sum(item.state == "met" for item in criteria), len(criteria))
    return Progress("unknown", None, None)


def required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class EvidenceSpecification:
    reference: str
    result: str | None = None

    def __post_init__(self) -> None:
        required(self.reference, "evidence reference")


@dataclass(frozen=True)
class Evidence:
    reference: str
    result: str | None


@dataclass(frozen=True)
class WorkItem:
    id: str
    project: str
    parent: str | None
    kind: str
    title: str
    goal: str
    condition: str
    resume_condition: str | None
    next_step: str | None
    focus: str | None
    created: datetime
    updated: datetime
    next_step_recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("project", "kind", "title", "goal"):
            required(getattr(self, name), name)
        if self.condition not in CONDITIONS:
            raise ValueError("unknown work condition")
        if self.condition == "waiting":
            required(self.resume_condition, "resume condition")
        if self.focus is not None:
            if self.kind != "epic":
                raise ValueError("focus is only valid for an epic")
            if self.focus not in ("priority", "background"):
                raise ValueError("focus must be priority or background")


@dataclass(frozen=True)
class Criterion:
    id: str
    work_item: str
    text: str
    verification: str
    specification: EvidenceSpecification | None
    state: str = "unmet"
    met_by: str | None = None
    evidence: tuple[str, ...] = ()
    met_at: datetime | None = None

    def __post_init__(self) -> None:
        required(self.text, "criterion text")
        if self.verification not in ("checked", "judged", "accepted"):
            raise ValueError("unknown verification kind")
        if self.verification == "checked" and self.specification is None:
            raise ValueError("checked criterion requires an evidence specification")

    def meet(self, actor: str, references: tuple[str, ...], records: list[Evidence], now: datetime) -> "Criterion":
        required(actor, "actor")
        if self.verification == "checked":
            spec = self.specification
            if not any(record.reference == spec.reference and record.reference in references
                       and (spec.result is None or record.result == spec.result) for record in records):
                raise ValueError("evidence does not match the specification")
        return replace(self, state="met", met_by=actor, evidence=references, met_at=now)


@dataclass(frozen=True)
class Relation:
    id: str
    from_item: str
    to_item: str
    type: str


@dataclass(frozen=True)
class Summary:
    id: str
    purpose: str
    done: str
    doing: str
    next: str
    authoring_role: str
    updated: datetime

    def __post_init__(self) -> None:
        required(self.authoring_role, "authoring role")
