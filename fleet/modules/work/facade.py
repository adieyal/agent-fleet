"""Public Work commands and records."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Callable
from fleet.modules.authority import AuthorityRejected

from .application import Commands
from .application.ports import EvidenceReader, WorkRepository
from .domain import KINDS, Criterion, EvidenceSpecification, Progress, Relation, Summary, WorkItem, accepted_progress


class WorkFacade:
    def __init__(self, repository: WorkRepository, evidence: EvidenceReader, clock: Callable[[], datetime],
                 *, authority=None, records=None) -> None:
        self.repository, self.clock = repository, clock
        self.commands = Commands(repository, evidence, clock)
        self.records = records
        self.authority = authority

    def add(self, *, project: str, title: str, goal: str, actor: str, kind: str = "task",
            parent: str | None = None, focus: str | None = None, next_step: str | None = None,
            plan: str | None = None) -> WorkItem:
        return self.commands.add(project=project, title=title, goal=goal, actor=actor, kind=kind,
            parent=parent, focus=focus, next_step=next_step, plan=plan, condition="none", resume_condition=None)

    def set(self, identity: str, *, actor: str, activation: str | None = None, **changes) -> WorkItem:
        authorization = None
        if activation is not None:
            if self.authority is None:
                raise AuthorityRejected('activation authority is not configured')
            authorization = self.authority().require('update_progress', identity, actor=actor, activation=activation)
            if changes.keys() - {'next_step', 'condition', 'resume_condition'}:
                raise AuthorityRejected('unsupported progress fields')
            if changes.get('condition') == 'complete':
                criteria = self.criteria(identity)
                if not criteria or any(c.state != 'met' for c in criteria):
                    raise AuthorityRejected('completion requires met criteria')
                self.authority().require('accept', identity, actor=actor, activation=activation)
        return self.commands.change(identity, actor, authorization=authorization, **changes)

    def apply_answer(self, identity: str, *, actor: str, next_step: str | None) -> WorkItem:
        return self.commands.apply_answer(identity, actor=actor, next_step=next_step)

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

    def criterion(self, identity: str) -> Criterion:
        return self.repository.get('criterion', identity)

    def meet(self, identity: str, *, actor: str, evidence: tuple[str, ...] = (),
             activation: str | None = None) -> Criterion:
        authorization = None
        if activation is not None:
            if self.authority is None:
                raise AuthorityRejected('activation authority is not configured')
            criterion = self.criterion(identity)
            authorization = self.authority().require('meet', criterion.work_item, actor=actor,
                                                     activation=activation, criterion=criterion)
        try:
            return self.commands.meet(identity, actor=actor, evidence=evidence, authorization=authorization)
        except ValueError as error:
            if activation is not None:
                raise AuthorityRejected(str(error)) from error
            raise

    def criteria(self, identity: str) -> list[Criterion]:
        return [item for item in self.repository.list("criterion") if item.work_item == identity]

    def criteria_by_item(self) -> dict[str, list[Criterion]]:
        """Every criterion grouped by its work item, read once: for views over many items."""
        grouped: dict[str, list[Criterion]] = {}
        for criterion in self.repository.list("criterion"):
            grouped.setdefault(criterion.work_item, []).append(criterion)
        return grouped

    def progress_within(self, item: WorkItem, items: list[WorkItem], criteria: list[Criterion]) -> Progress:
        """progress() from records already read: `items` is the item's project, `criteria` its own."""
        return accepted_progress([child for child in items if child.parent == item.id], criteria)

    def summaries(self, items: list[WorkItem]) -> dict[str, Summary]:
        """summary() for many items at once, reading the legacy summary table a single time."""
        legacy = {summary.id: summary for summary in self.repository.list("summary")}
        found = {}
        for item in items:
            summary = self.recorded_summary(item) or legacy.get(item.id)
            if summary is not None:
                found[item.id] = summary
        return found

    def recorded_summary(self, item: WorkItem) -> Summary | None:
        if self.records is None:
            return None
        body = self.records.read(item.project, f'summaries/{item.id}.json')
        if body is None:
            return None
        fields = json.loads(body)
        fields['updated'] = datetime.fromisoformat(fields['updated'])
        return Summary(**fields)

    def relate(self, from_item: str, to_item: str, *, actor: str, type: str = "depends-on") -> Relation:
        return self.commands.relate(from_item, to_item, type=type, actor=actor)

    def relations(self, identity: str) -> list[Relation]:
        return [item for item in self.repository.list("relation") if identity in (item.from_item, item.to_item)]

    def relations_by_item(self) -> dict[str, list[Relation]]:
        """Every relation grouped by the item it starts from, read once: for views over many items."""
        grouped: dict[str, list[Relation]] = {}
        for relation in self.repository.list("relation"):
            grouped.setdefault(relation.from_item, []).append(relation)
        return grouped

    def set_summary(self, identity: str, *, purpose: str, done: str, doing: str, next: str,
                    authoring_role: str, actor: str, activation: str | None = None,
                    source_run: str | None = None) -> Summary:
        item = self.get(identity)
        authorization = None
        if activation is not None:
            if self.authority is None:
                raise AuthorityRejected('activation authority is not configured')
            authorization = self.authority().require('summary', identity, actor=actor, activation=activation)
        if self.records is None:
            raise ValueError('Records authoring is required')
        summary = Summary(identity, purpose, done, doing, next, authoring_role, self.clock(), activation,
                          None if authorization is None else authorization.mandate_version)
        self.records.write_summary(summary, item.project, actor=actor, source_run=source_run)
        return summary

    def summary(self, identity: str) -> Summary | None:
        recorded = self.recorded_summary(self.get(identity)) if self.records is not None else None
        return recorded or next((item for item in self.repository.list("summary") if item.id == identity), None)

    def legacy_summaries(self, project: str) -> list[Summary]:
        identities = {item.id for item in self.list(project=project)}
        return [summary for summary in self.repository.list('summary') if summary.id in identities]

    def retire_summary(self, identity: str, *, actor: str) -> None:
        self.repository.retire_summary(identity, actor)
