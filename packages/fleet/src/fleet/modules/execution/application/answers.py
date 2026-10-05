"""Answer a job step that ended asking its supervisor, by adding a step with the reply to the job."""

from typing import Callable
from datetime import datetime
from uuid import uuid4

from .dtos import AnswerRequest
from .ports import AnswerSender, ExecutionRepository


def answer(repository: ExecutionRepository, send: AnswerSender, item_id: str, reply: str, actor: str,
           clock: Callable[[], datetime], work_item: str | None = None,
           require_step_work: Callable[[str, str, str], None] | None = None) -> str:
    from fleet.modules.decisions import Decision

    if not actor.strip():
        raise ValueError("actor is required")
    if not reply.strip():
        raise ValueError("an answer is required")
    with repository.transaction() as transaction:
        item = transaction.attention.get(item_id)
        if item.state == "resolved":
            raise transaction.attention.resolved_answer_error(item, reply, actor)
    context = item.stream_context
    if context is None or not context.blocked_step:
        raise ValueError("only a blocked job step can be answered here")
    if work_item is not None:
        if require_step_work is None:
            raise RuntimeError("step work items cannot be checked here")
        require_step_work(context.host, context.owner_id, work_item)
    # The worker adds a key's step once, so a retry after a lost reply queues nothing more.
    continuation = send(AnswerRequest(context.host, context.owner_id, context.step, f"{item.id}:answer", reply,
                                      work_item))
    details = f"answered; step {context.step + 1} continues as step {continuation + 1}"
    with repository.transaction() as transaction:
        current = transaction.attention.get(item.id)
        if current.state == 'resolved':
            raise transaction.attention.resolved_answer_error(current, reply, actor)
        run = transaction.find(context.host, context.owner_id)
        affected = work_item or item.work_item
        if affected is None and run is not None:
            affected = next((step['work_item'] for step in run.step_work or [] if step['index'] == context.step), None)
            affected = affected or transaction.get_action(run.action).work_item
        record = Decision(str(uuid4()), item.id, item.headline, reply, actor, item.context_reference,
                          () if affected is None else (affected,), clock(), source_run=run.id if run else None)
        transaction.record_answer_decision(record)
        transaction.attention.resolve(item.id, details=details, actor=actor)
    return details
