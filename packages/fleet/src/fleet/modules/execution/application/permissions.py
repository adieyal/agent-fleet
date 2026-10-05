"""Answer a job step's permission refusals by allowing rules for the job, then continuing the step."""

from typing import Callable

from fleet.modules.attention import AttentionItem, ItemResolved, refusal_rules

from .dtos import GrantRequest
from .ports import ExecutionRepository, GrantSender

SCOPES = ("refused", "bash")


def grant_rules(item, scope: str) -> tuple[list[str], list[str]]:
    """The rules to add, and the refused requests they leave refused (a deny rule outranks any allow)."""
    denied = [f"{refusal.detail} (denied by {', '.join(refusal.denied_by)})" for refusal in item.refusals
              if refusal.denied_by]
    if denied and len(denied) == len(item.refusals):
        raise ValueError(f"a deny rule refuses {'this request' if len(denied) == 1 else 'these requests'}, and no "
                         f"rule allowed for the job can override it: {'; '.join(denied)}. Remove that deny rule "
                         "to let jobs run it")
    if scope == "bash":
        return ["Bash"], denied + [refusal.detail for refusal in item.refusals
                                   if refusal.tool != "Bash" and not refusal.denied_by]
    rules = refusal_rules(item.refusals)
    if rules is None:
        raise ValueError(f"fleetd on {item.stream_context.host} names no rules for these requests; "
                         "upgrade it, or allow all Bash")
    if not rules:
        raise ValueError("no permission rule matches these requests")
    return rules, denied + [refusal.detail for refusal in item.refusals if not refusal.rules and not refusal.denied_by]


def grant(repository: ExecutionRepository, send: GrantSender, item_id: str, scope: str, actor: str, *,
          authorize: Callable[[AttentionItem], None] | None = None,
          complete: Callable[[AttentionItem, str], None] | None = None) -> str:
    if scope not in SCOPES:
        raise ValueError("scope must be refused or bash")
    with repository.transaction() as transaction:
        item = transaction.attention.get(item_id)
    context = item.stream_context
    if not item.refusals or context is None or context.owner_type != "job" or context.step is None:
        raise ValueError("only a job step's permission refusals can be allowed")
    if item.state == "resolved":
        raise ItemResolved("attention item is resolved")
    if authorize is not None:
        authorize(item)
    rules, uncovered = grant_rules(item, scope)
    # The worker applies a key once, so a retry after a lost reply queues nothing more.
    result = send(GrantRequest(context.host, context.owner_id, context.step, f"{item.id}:{scope}", tuple(rules)))
    details = (f"allowed for job {context.owner_id}: {', '.join(rules)}; "
               f"step {context.step + 1} continues as step {result.continuation + 1}")
    if uncovered:
        details += f"; still not allowed: {'; '.join(uncovered)}"
    if complete is not None:
        complete(item, details)
        return details
    with repository.transaction() as transaction:
        # Recorded even if the step ended meanwhile: the grant is what happened.
        transaction.attention.resolve(item.id, details=details, actor=actor)
    return details
