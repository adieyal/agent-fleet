"""Pure first-observation routing. Refusal rules are compared exactly, never broadened."""

from dataclasses import dataclass

from fleet.modules.records import TriageMandate
from . import Refusal, StreamContext, refusal_rules


@dataclass(frozen=True)
class RoutingHistory:
    """Controller evidence for a subject and step; populated by the triage controller."""
    triage_run: bool = False
    retry_decisions: tuple[str, ...] = ()


def refusal_violation(refusals: tuple[Refusal, ...], mandate: TriageMandate) -> str | None:
    """Why this batch cannot be granted by triage; None means every rule is allowed."""
    if any(refusal.denied_by for refusal in refusals):
        return 'outside the triage mandate: a deny rule refused the request'
    rules = refusal_rules(refusals)
    if not rules or any(not refusal.rules for refusal in refusals):
        return 'outside the triage mandate: refusal rules are missing'
    for rule in rules:
        if rule in mandate.permissions['escalate']:
            return f'outside the triage mandate: {rule} is listed under escalate'
        if rule not in mandate.permissions['allow']:
            return f'outside the triage mandate: {rule} is not allowed'
    return None


def route(kind: str, context: StreamContext | None, mandate: TriageMandate | None,
          history: RoutingHistory = RoutingHistory(), *, refusals: tuple[Refusal, ...] = (),
          reason: str | None = None) -> tuple[str, str | None]:
    if mandate is None:
        return 'user', reason
    if context is None or context.owner_type != 'job':
        return 'user', reason
    if history.triage_run:
        return 'user', "the triage agent's own run needs attention"
    if refusals:
        violation = refusal_violation(refusals, mandate)
        if violation is not None:
            return 'user', violation
        return mandate.routing.get('refusal', 'agent'), None
    status = context.source.removeprefix('job status ')
    if kind != 'blocker' or status not in ('failed', 'stalled', 'lost', 'blocked'):
        return 'user', reason
    if status != 'blocked' and len(history.retry_decisions) >= mandate.limits['retries_per_step']:
        return 'user', (f'failed again after {len(history.retry_decisions)} agent retries '
                        f'(decisions {", ".join(history.retry_decisions)})')
    owner = mandate.routing.get(status, 'user' if status == 'blocked' else 'agent')
    return owner, 'the job asked its supervisor' if status == 'blocked' and owner == 'user' else None
