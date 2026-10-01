from dataclasses import dataclass


DECISION_AUTHORITY_COMMANDS = (
    'update_progress', 'raise_attention', 'dispatch', 'summary', 'record_decision', 'accept',
)


class AuthorityRejected(ValueError):
    """The command exceeds the activation's authority."""


@dataclass(frozen=True)
class Activation:
    id: str
    actor: str
    role: str
    project: str
    work_item: str | None
    mandate_path: str
    mandate_version: str

    def __post_init__(self) -> None:
        if self.role not in ('orchestrator', 'triage'):
            raise AuthorityRejected('role must be orchestrator or triage')
        if self.work_item is None and self.role != 'triage':
            raise AuthorityRejected('only triage may omit a work item')
        if not self.actor.strip() or not self.project.strip():
            raise AuthorityRejected('actor and project are required')


def require_scope(activation: Activation, actor: str, work_item: str | None) -> None:
    if actor != activation.actor:
        raise AuthorityRejected('actor does not own activation')
    if work_item != activation.work_item:
        raise AuthorityRejected('work item is outside activation scope')


def require_command(mandate, command: str, criterion=None) -> None:
    if command == 'meet':
        if criterion.verification == 'accepted':
            raise AuthorityRejected('criterion is reserved for the user')
        if criterion.verification == 'judged' and criterion.id not in mandate.criteria_it_may_judge:
            raise AuthorityRejected('mandate may not judge this criterion')
    elif command != 'propose' and (
        command not in DECISION_AUTHORITY_COMMANDS or command not in mandate.decision_authority
    ):
        raise AuthorityRejected(f'mandate does not authorize {command}')
