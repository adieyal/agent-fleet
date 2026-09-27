"""Public Work commands and records."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .application import Commands
from .application.ports import EvidenceReader, WorkRepository
from .domain import KINDS, Criterion, EvidenceSpecification, Progress, Relation, Summary, WorkItem, accepted_progress


class WorkFacade:
    def __init__(self, repository: WorkRepository, evidence: EvidenceReader, clock: Callable[[], datetime]) -> None:
        self.repository, self.clock = repository, clock
        self.commands = Commands(repository, evidence, clock)

    def add(self, *, project: str, title: str, goal: str, actor: str, kind: str = "task",
            parent: str | None = None, focus: str | None = None, next_step: str | None = None) -> WorkItem:
        return self.commands.add(project=project, title=title, goal=goal, actor=actor, kind=kind,
            parent=parent, focus=focus, next_step=next_step, condition="none", resume_condition=None)

    def set(self, identity: str, *, actor: str, **changes) -> WorkItem:
        return self.commands.change(identity, actor, **changes)

    def move(self, identity: str, *, parent: str | None, actor: str) -> WorkItem:
        return self.commands.change(identity, actor, parent=parent)

    def ready(self, identity: str, *, actor: str) -> WorkItem:
        return self.commands.change(identity, actor, ready=True)

    def get(self, identity: str) -> WorkItem:
        return self.repository.get("item", identity)

    def list(self, *, project: str | None = None) -> list[WorkItem]:
        return [item for item in self.repository.list("item") if project is None or item.project == project]

    def kinds(self, project: str) -> list[str]:
        return sorted(set(KINDS) | {item.kind for item in self.list(project=project)})

    def progress(self, identity: str) -> Progress:
        item = self.get(identity)
        children = [child for child in self.list(project=item.project) if child.parent == identity]
        return accepted_progress(children, self.criteria(identity))

    def add_criterion(self, identity: str, *, text: str, verification: str, actor: str,
                      specification: EvidenceSpecification | None = None) -> Criterion:
        return self.commands.add_criterion(identity, text=text, verification=verification,
                                           specification=specification, actor=actor)

    def meet(self, identity: str, *, actor: str, evidence: tuple[str, ...] = ()) -> Criterion:
        return self.commands.meet(identity, actor=actor, evidence=evidence)

    def criteria(self, identity: str) -> list[Criterion]:
        return [item for item in self.repository.list("criterion") if item.work_item == identity]

    def relate(self, from_item: str, to_item: str, *, actor: str, type: str = "depends-on") -> Relation:
        return self.commands.relate(from_item, to_item, type=type, actor=actor)

    def relations(self, identity: str) -> list[Relation]:
        return [item for item in self.repository.list("relation") if identity in (item.from_item, item.to_item)]

    def set_summary(self, identity: str, *, purpose: str, done: str, doing: str, next: str,
                    authoring_role: str, actor: str) -> Summary:
        return self.commands.set_summary(identity, purpose=purpose, done=done, doing=doing, next=next,
                                          authoring_role=authoring_role, actor=actor)

    def summary(self, identity: str) -> Summary | None:
        return next((item for item in self.repository.list("summary") if item.id == identity), None)
