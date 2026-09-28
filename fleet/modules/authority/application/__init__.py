from uuid import uuid4
from .ports import ActivationRepository

from ..domain import Activation, AuthorityRejected, require_command, require_scope


class Commands:
    def __init__(self, repository: ActivationRepository, records, work):
        self.repository, self.records, self.work = repository, records, work

    def activate(self, work_item: str, *, actor: str, role: str, mandate_path: str) -> Activation:
        if not actor.strip() or role != 'orchestrator':
            raise AuthorityRejected('actor and orchestrator role are required')
        item = self.work.get(work_item)
        version, mandate = self.records.mandate_version(item.project, mandate_path)
        criteria = {criterion.id for criterion in self.work.criteria(item.id)}
        unknown = [identity for identity in mandate.criteria_it_may_judge if identity not in criteria]
        if unknown:
            raise AuthorityRejected(f'criteria_it_may_judge entries are not criteria of work item {item.id}: '
                                    f'{", ".join(unknown)}')
        activation = Activation(str(uuid4()), actor, role, item.project, item.id, mandate_path, version)
        self.repository.insert(activation)
        return activation

    def require(self, command: str, work_item: str, *, actor: str, activation: str, criterion=None) -> Activation:
        try:
            context = self.repository.get(activation)
        except LookupError as error:
            raise AuthorityRejected('unknown activation') from error
        require_scope(context, actor, work_item)
        _, mandate = self.records.mandate_version(context.project, context.mandate_path,
                                                  revision=context.mandate_version)
        require_command(mandate, command, criterion)
        return context
