"""Answer across Decisions, Attention and Work in one unit of work."""

from datetime import datetime
from typing import Callable
from uuid import uuid4
from dataclasses import asdict
import json

from .ports import DecisionRepository
from ..domain import Decision, Proposal, selected_answer


def record_decision(repository, clock, records, authorization, source_run: str,
                    question: str, answer: str, context: str, principle: str | None = None) -> Decision:
    with repository.transaction() as transaction:
        decision = Decision(str(uuid4()), None, question, answer, authorization.actor, context,
                            (authorization.work_item,), clock(), authorization.id,
                            authorization.mandate_version, source_run, principle,
                            run_guidance(transaction.execution, source_run))
        body = json.dumps(asdict(decision), default=str)
        transaction.insert(decision)
        intent = transaction.records.prepare(authorization.project, f'decisions/{decision.id}.json',
            body, key=decision.id, actor=authorization.actor, source_run=source_run)
    records.publish(intent, body)
    return decision


def run_guidance(execution, source_run: str | None) -> dict | None:
    """The guidance versions pinned on the run's dispatch; None when there was no run or no guidance."""
    if source_run is None:
        return None
    return execution.get_action(execution.get_run(source_run).action).guidance


def record_guided(repository, clock, work_item: str, *, actor: str, question: str, answer: str,
                  principle: str, context: str, source_run: str | None) -> Decision:
    """A decision an agent made itself under guidance, without an activation."""
    return insert_guided(repository, str(uuid4()), clock(), work_item, actor=actor, question=question,
                         answer=answer, principle=principle, context=context, source_run=source_run)


def record_streamed(repository, identity: str, time: datetime, work_item: str, *, actor: str, question: str,
                    answer: str, principle: str, context: str, source_run: str | None) -> Decision:
    """A guided decision made on another host, under the id and time it was made with: recorded once per id."""
    try:
        return repository.get(identity)
    except LookupError:
        return insert_guided(repository, identity, time, work_item, actor=actor, question=question,
                             answer=answer, principle=principle, context=context, source_run=source_run)


def insert_guided(repository, identity: str, time: datetime, work_item: str, *, actor: str, question: str,
                  answer: str, principle: str, context: str, source_run: str | None) -> Decision:
    if not question.strip() or not principle.strip():
        raise ValueError("question and principle are required")
    with repository.transaction() as transaction:
        project = transaction.work.get(work_item).project
        if source_run is not None:
            run = transaction.execution.get_run(source_run)
            if transaction.execution.get_action(run.action).project != project:
                raise ValueError(f"run {source_run} is not in {project}, the project of {work_item}")
        decision = Decision(identity, None, question, answer, actor, context, (work_item,), time,
                            source_run=source_run, principle=principle,
                            guidance=run_guidance(transaction.execution, source_run))
        transaction.insert(decision)
        return decision


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
        if item.refusals:
            raise ValueError("a job's permission refusals are answered by allowing or dismissing them")
        if item.questions:
            raise ValueError("a session's question is answered in its terminal")
        if item.stream_context is not None and item.stream_context.blocked_step:
            raise ValueError("a blocked job step is answered by a step added to its job (execution answer_blocked)")
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
