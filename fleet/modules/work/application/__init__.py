"""Transactional Work command workflows."""

from dataclasses import replace
from datetime import datetime
from typing import Callable
from uuid import uuid4

from .ports import EvidenceReader, WorkRepository
from ..domain import RELATION_TYPES, Criterion, Relation, WorkItem, required


class Commands:
    def __init__(self, repository: WorkRepository, evidence: EvidenceReader, clock: Callable[[], datetime]) -> None:
        self.repository, self.evidence, self.clock = repository, evidence, clock

    def parent(self, repository: WorkRepository, item: WorkItem) -> None:
        identity = item.parent
        seen = {item.id}
        while identity is not None:
            if identity in seen:
                raise ValueError("work parent cycle")
            seen.add(identity)
            parent = repository.get("item", identity)
            if parent.project != item.project:
                raise ValueError("parent must belong to the same project")
            identity = parent.parent

    def add(self, *, actor: str, **fields) -> WorkItem:
        required(actor, "actor")
        now = self.clock()
        item = WorkItem(id=str(uuid4()), created=now, updated=now,
                        next_step_recorded_at=now if fields["next_step"] is not None else None, **fields)
        with self.repository.transaction() as repository:
            self.parent(repository, item)
            repository.save("item", item, actor)
        return item

    def change(self, identity: str, actor: str, *, ready: bool = False, authorization=None, **changes) -> WorkItem:
        required(actor, "actor")
        allowed = {"title", "goal", "condition", "resume_condition", "next_step", "focus", "kind", "parent", "plan"}
        if changes.keys() - allowed:
            raise ValueError("unknown work fields")
        changes['activation'] = None if authorization is None else authorization.id
        changes['mandate_version'] = None if authorization is None else authorization.mandate_version
        with self.repository.transaction() as repository:
            previous = repository.get("item", identity)
            if ready:
                if previous.condition != "waiting":
                    raise ValueError("only waiting work can become ready")
                changes["condition"] = "ready for review"
            now = self.clock()
            if "next_step" in changes:
                changes["next_step_recorded_at"] = now if changes["next_step"] is not None else None
            item = replace(previous, **changes, updated=now)
            self.parent(repository, item)
            if item.condition == "blocked" and previous.condition != "blocked":
                repository.attention.raise_item(project=item.project, work_item=item.id, kind="blocker",
                    owner="user", source="work", source_reference=item.id, headline="Work item blocked",
                    context_reference=f"work:{item.id}", actor=actor, reopen=True)
            elif previous.condition == "blocked" and item.condition != "blocked":
                for blocker in repository.attention.list(project=item.project):
                    if blocker.source == "work" and blocker.source_reference == item.id:
                        repository.attention.resolve(blocker.id, details="Work item unblocked", actor=actor)
            repository.save("item", item, actor)
        return item

    def apply_answer(self, identity: str, *, actor: str, next_step: str | None) -> WorkItem:
        required(actor, "actor")
        with self.repository.transaction() as repository:
            item = repository.get("item", identity)
            changes = {}
            if item.condition == "blocked":
                changes["condition"] = "none"
            if next_step is not None:
                changes["next_step"] = next_step
            if changes:
                return self.change(identity, actor, **changes)
            return item

    def add_criterion(self, identity: str, *, actor: str, **fields) -> Criterion:
        required(actor, "actor")
        criterion = Criterion(id=str(uuid4()), work_item=identity, **fields)
        with self.repository.transaction() as repository:
            repository.get("item", identity)
            repository.save("criterion", criterion, actor)
        return criterion

    def meet(self, identity: str, *, actor: str, evidence: tuple[str, ...], authorization=None) -> Criterion:
        required(actor, "actor")
        with self.repository.transaction() as repository:
            criterion = repository.get("criterion", identity)
            records = [record for reference in evidence if (record := self.evidence.get(reference)) is not None]
            criterion = criterion.meet(actor, evidence, records, self.clock())
            criterion = replace(criterion, activation=None if authorization is None else authorization.id,
                                mandate_version=None if authorization is None else authorization.mandate_version)
            repository.save("criterion", criterion, actor)
        return criterion

    def relate(self, from_item: str, to_item: str, *, type: str, actor: str) -> Relation:
        required(actor, "actor")
        if type not in RELATION_TYPES:
            raise ValueError(f"unknown relation type: use one of {', '.join(RELATION_TYPES)}")
        if from_item == to_item:
            raise ValueError("cannot relate a work item to itself")
        with self.repository.transaction() as repository:
            repository.get("item", from_item)
            repository.get("item", to_item)
            relation = Relation(str(uuid4()), from_item, to_item, type)
            repository.save("relation", relation, actor)
        return relation
