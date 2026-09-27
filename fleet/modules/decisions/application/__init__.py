"""Answer across Decisions, Attention and Work in one unit of work."""

from datetime import datetime
from typing import Callable
from uuid import uuid4

from .ports import DecisionRepository
from ..domain import Decision, Proposal, selected_answer


def propose(repository, clock, activation, *, question: str, change: str, reason: str) -> Proposal:
    proposal = Proposal(str(uuid4()), activation.project, activation.work_item, activation.actor,
                        activation.id, activation.mandate_version, question, change, reason, clock())
    with repository.transaction() as transaction:
        transaction.insert_proposal(proposal)
        transaction.attention.raise_item(project=proposal.project, work_item=proposal.work_item,
            kind='decision', owner='user', source='proposal', source_reference=proposal.id,
            headline=question, context_reference='proposal:' + proposal.id, actor=proposal.actor)
    return proposal


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
