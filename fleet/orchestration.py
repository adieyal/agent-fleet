"""Local orchestrator command delivery through controller facades."""

from fleet import composition
from fleet.projections.project import project_status
from dataclasses import asdict
import json


def orchestrator_prompt(activation, mandate) -> str:
    return f'''Act as the orchestrator for work item {activation.work_item}.
Mandate version: {activation.mandate_version}
Mandate: {json.dumps(asdict(mandate))}
Use only this command surface for controller writes:
fleet control {activation.id} COMMAND 'JSON'
Commands and JSON fields:
state: {{}}
progress: next_step, condition, resume_condition (only changed fields)
meet: criterion, evidence (array of recorded references)
attention: headline, context_reference
decide: question, answer, context
summary: purpose, done, doing, next
propose: question, change, reason
dispatch: host, runtime, reason, idempotency_key, payload
Dispatch payload: cwd, arguments (fleetd create arguments including --hold and
--steps-file /dev/stdin), steps (prompt/title objects), context (paths or null), hold (boolean).
Read state first. Record routine decisions without attention. Rejected writes do
not change state; explicitly propose changes requiring the user's authority.
Leave a next step or named condition. A successful run never completes work.
Completion needs all criteria met and explicit accept authority.
'''


class ControllerCommands:
    def __init__(self, store, activation: str):
        self.services = composition.facades(store)
        self.activation = self.services.authority.get(activation)

    def execute(self, command: str, payload: dict):
        services, activation = self.services, self.activation
        context = dict(actor=activation.actor, activation=activation.id)
        if command == 'state':
            return project_status(activation.project, services.work, services.attention,
                                  services.execution, services.library, services.decisions)
        if command == 'progress':
            return services.work.set(activation.work_item, **context, **payload)
        if command == 'meet':
            return services.work.meet(payload['criterion'], evidence=tuple(payload['evidence']), **context)
        if command == 'attention':
            return services.authority.raise_attention(**context, **payload)
        if command == 'propose':
            return services.authority.propose(**context, **payload)
        if command == 'dispatch':
            return services.execution.dispatch(activation.work_item, **context, **payload)
        if command in ('summary', 'decide'):
            run = services.execution.activation_run(activation.id, activation.id)
            if command == 'summary':
                return services.work.set_summary(activation.work_item, **context, source_run=run.id,
                                                 authoring_role=activation.role, **payload)
            return services.decisions.record(activation.work_item, **context, source_run=run.id, **payload)
        raise ValueError('unknown orchestrator command')
