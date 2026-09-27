"""Answer across Decisions, Attention and Work in one unit of work."""

from datetime import datetime
from typing import Callable
from uuid import uuid4

from .ports import DecisionRepository
from ..domain import Decision, selected_answer


def answer_question(repository: DecisionRepository, clock: Callable[[], datetime], identity: str,
                    answer: str, actor: str, next_step: str | None) -> Decision:
    with repository.transaction() as transaction:
        item = transaction.attention.get(identity)
        if item.state == "resolved":
            raise ValueError("attention item is resolved")
        decision = Decision(str(uuid4()), item.id, item.headline,
            selected_answer(answer, item.options), actor, item.context_reference,
            () if item.work_item is None else (item.work_item,), clock())
        if next_step is not None and item.work_item is None:
            raise ValueError("next step requires an affected work item")
        transaction.insert(decision)
        transaction.attention.resolve(item.id, details=f"decision:{decision.id}", actor=actor)
        for identity in decision.affected_work_items:
            transaction.work.apply_answer(identity, actor=actor, next_step=next_step)
        transaction.execution.queue_answer(item, decision)
        return decision
