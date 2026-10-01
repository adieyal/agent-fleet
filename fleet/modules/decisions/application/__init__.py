"""Answer across Decisions, Attention and Work in one unit of work."""

from datetime import datetime
from typing import Callable, TYPE_CHECKING
from uuid import uuid4
from dataclasses import asdict, replace
import json

from fleet.modules.authority import AuthorityRejected
from fleet.modules.attention import AttentionItem

from .ports import DecisionRepository
from ..domain import Decision, Proposal, selected_answer

if TYPE_CHECKING:
    from fleet.modules.authority import AuthorityFacade
    from fleet.modules.records import RecordsFacade


def escalation_snapshot(repository: DecisionRepository, item: AttentionItem, activation: str) -> AttentionItem:
    """Permit reopening only the resolution produced by this activation's recorded action."""
    if item.owner != 'agent' or item.state != 'resolved':
        return item
    identity = (item.resolution_details or '').rpartition('; decision:')[2]
    if not identity:
        return item
    try:
        previous = repository.get(identity)
    except LookupError:
        return item
    if previous.attention_item != item.id or previous.activation != activation:
        return item
    evidence = json.loads(previous.context)
    if evidence.get('command') not in ('retry', 'add_step', 'grant'):
        return item
    return replace(item, state='open')


def record_attention(repository: DecisionRepository, clock: Callable[[], datetime], records: 'RecordsFacade',
                     authority: 'AuthorityFacade', item_id: str, *, actor: str,
                     activation: str, source_run: str, command: str, answer: str, principle: str,
                     context: str, question: str | None = None, effect: str | None = None,
                     completed_item: AttentionItem | None = None, retry_run: str | None = None) -> Decision:
    """Record triage and its local effect together; completed_item audits an already sent remote effect.

    If ownership or a refusal batch changed during transport, keep that current state while recording
    what happened to the previously authorized snapshot.
    """
    if not principle.strip():
        raise ValueError('principle is required')
    if effect not in (None, 'resolve', 'escalate'):
        raise ValueError('unknown triage attention effect')
    with repository.transaction() as transaction:
        item = transaction.attention.get(item_id)
        authorized_item = (escalation_snapshot(transaction, item, activation) if command == 'escalate'
                           else item if completed_item is None else completed_item)
        if (authorized_item.id, authorized_item.project) != (item.id, item.project):
            raise ValueError('completed effect does not belong to this attention item')
        authorization = authority.require_triage(command, authorized_item, actor=actor, activation=activation)
        run = transaction.execution.get_run(source_run)
        action = transaction.execution.get_action(run.action)
        if action.activation != activation or action.project != item.project:
            raise ValueError('source run is not this triage activation')
        requested = json.loads(context)
        target = None if retry_run is None else transaction.execution.get_run(retry_run)
        if target is not None:
            # execution.retry creates a new remote job. Its action remains stable across the chain.
            requested['retry_action'] = target.action
            context = json.dumps(requested)
        if command == 'retry':
            previous_retries = []
            for previous in transaction.list():
                if previous.activation is None:
                    continue
                try:
                    evidence = json.loads(previous.context)
                except ValueError:
                    continue
                if not isinstance(evidence, dict) or evidence.get('command') != 'retry':
                    continue
                if retry_run is None and previous.attention_item == item.id:
                    raise AuthorityRejected('legacy retry was already requested; inspect or escalate, do not resend')
                identity = ('project', 'subject', 'step') if target is None else ('project', 'retry_action', 'step')
                if all(evidence.get(name) == requested.get(name) for name in identity):
                    previous_retries.append(previous.id)
            limit = authority.triage_mandate(activation).limits['retries_per_step']
            if len(previous_retries) >= limit:
                raise AuthorityRejected(f'retry limit {limit} reached (decisions {", ".join(previous_retries)})')
        if retry_run is not None:
            if command != 'retry':
                raise ValueError('only retry may queue a run')
            stream = item.stream_context
            if stream is None or (target.host, target.remote_job_id) != (stream.host, stream.owner_id):
                raise ValueError('retry run does not belong to the attention subject')
            result = transaction.execution.retry(retry_run, actor=actor, idempotency_key=f'triage-retry:{item.id}')
            answer = f'retry queued as run {result.run.id}: {answer}'
            context = json.dumps(dict(json.loads(context), run=result.run.id, created=result.created))
        decision = Decision(str(uuid4()), item.id, authorized_item.headline if question is None else question,
                            answer, actor, context, () if authorized_item.work_item is None else (authorized_item.work_item,),
                            clock(), activation, authorization.mandate_version, source_run, principle,
                            run_guidance(transaction.execution, source_run))
        transaction.insert(decision)
        if command == 'escalate' and item.state == 'resolved' and authorized_item.state == 'open':
            item = transaction.attention.reopen_for_escalation(item.id, actor=actor)
        if item.owner == 'agent' and item.state != 'resolved' and item.refusals == authorized_item.refusals:
            if effect == 'resolve':
                transaction.attention.resolve(item.id, details=f'{answer}; decision:{decision.id}', actor=actor)
            elif effect == 'escalate':
                transaction.attention.escalate(item.id, reason=answer.removeprefix('escalated to the user: '), actor=actor)
        body = json.dumps(asdict(decision), default=str)
        intent = transaction.records.prepare(item.project, f'decisions/{decision.id}.json', body,
                                             key=decision.id, actor=actor, source_run=source_run)
    records.publish(intent, body)
    return decision


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
        if item.questions or item.at_terminal:
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
