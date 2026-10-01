from uuid import uuid4
from .ports import ActivationRepository

from ..domain import Activation, AuthorityRejected, require_command, require_scope


class Commands:
    def __init__(self, repository: ActivationRepository, records, work):
        self.repository, self.records, self.work = repository, records, work

    def activate(self, work_item: str | None = None, *, actor: str, role: str, mandate_path: str,
                 project: str | None = None) -> Activation:
        if role == 'triage':
            if work_item is not None or project is None or not project.strip():
                raise AuthorityRejected('triage requires a project and no work item')
            if mandate_path != 'mandates/triage.json':
                raise AuthorityRejected('triage requires mandates/triage.json')
            version, mandate = self.records.mandate_version(project, mandate_path)
            if mandate.criteria_it_may_judge:
                raise AuthorityRejected('triage may not judge work criteria')
            identity = str(uuid4())
            activation = Activation(identity, 'triage:' + identity if actor == 'triage' else actor,
                                    role, project, None, mandate_path, version)
            self.repository.insert(activation)
            return activation
        if not actor.strip() or role != 'orchestrator' or work_item is None:
            raise AuthorityRejected('actor, work item and orchestrator role are required')
        if mandate_path == 'mandates/triage.json':
            raise AuthorityRejected('triage mandate requires the triage role')
        item = self.work.get(work_item)
        if project is not None and project != item.project:
            raise AuthorityRejected('work item is outside project scope')
        version, mandate = self.records.mandate_version(item.project, mandate_path)
        criteria = {criterion.id for criterion in self.work.criteria(item.id)}
        unknown = [identity for identity in mandate.criteria_it_may_judge if identity not in criteria]
        if unknown:
            raise AuthorityRejected(f'criteria_it_may_judge entries are not criteria of work item {item.id}: '
                                    f'{", ".join(unknown)}')
        activation = Activation(str(uuid4()), actor, role, item.project, item.id, mandate_path, version)
        self.repository.insert(activation)
        return activation

    def require(self, command: str, work_item: str | None, *, actor: str, activation: str, criterion=None) -> Activation:
        try:
            context = self.repository.get(activation)
        except LookupError as error:
            raise AuthorityRejected('unknown activation') from error
        require_scope(context, actor, work_item)
        _, mandate = self.records.mandate_version(context.project, context.mandate_path,
                                                  revision=context.mandate_version)
        if context.role == 'triage':
            # Launching the activation is a controller operation, not an agent's dispatch authority.
            # Execution additionally checks the launch against the pinned host/runtime/cwd.
            if command != 'dispatch' or work_item is not None:
                raise AuthorityRejected('triage uses attention controls, not work-item controls')
            return context
        require_command(mandate, command, criterion)
        return context

    def triage_mandate(self, activation: str):
        context = self.repository.get(activation)
        if context.role != 'triage':
            raise AuthorityRejected('triage activation required')
        _, mandate = self.records.mandate_version(context.project, context.mandate_path,
                                                  revision=context.mandate_version)
        return mandate

    def require_triage(self, command: str, item, *, actor: str, activation: str) -> Activation:
        context = self.repository.get(activation)
        mandate = self.triage_mandate(activation)
        if actor != context.actor:
            raise AuthorityRejected('actor does not own activation')
        if item.project != context.project:
            raise AuthorityRejected('attention item is outside activation project')
        if item.owner != 'agent' or item.state == 'resolved':
            raise AuthorityRejected('triage requires an unresolved agent-owned attention item')
        if command not in mandate.decision_authority:
            raise AuthorityRejected(f'mandate does not authorize {command}')
        if (item.stream_context is not None and item.stream_context.owner_type == 'session'
                and command not in ('escalate', 'record_decision')):
            raise AuthorityRejected('session input must be answered in its terminal')
        return context
