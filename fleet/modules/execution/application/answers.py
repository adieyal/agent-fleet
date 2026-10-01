"""Answer a job step that ended asking its supervisor, by adding a step with the reply to the job."""

from fleet.modules.attention import ItemResolved

from .dtos import AnswerRequest
from .ports import AnswerSender, ExecutionRepository


def answer(repository: ExecutionRepository, send: AnswerSender, item_id: str, reply: str, actor: str) -> str:
    if not reply.strip():
        raise ValueError("an answer is required")
    with repository.transaction() as transaction:
        item = transaction.attention.get(item_id)
    context = item.stream_context
    if context is None or not context.blocked_step:
        raise ValueError("only a blocked job step can be answered here")
    if item.state == "resolved":
        raise ItemResolved("attention item is resolved")
    # The worker adds a key's step once, so a retry after a lost reply queues nothing more.
    continuation = send(AnswerRequest(context.host, context.owner_id, context.step, f"{item.id}:answer", reply))
    details = f"answered; step {context.step + 1} continues as step {continuation + 1}"
    with repository.transaction() as transaction:
        transaction.attention.resolve(item.id, details=details, actor=actor)
    return details
