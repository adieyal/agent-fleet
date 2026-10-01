"""Local orchestrator command delivery through controller facades."""

from fleet import composition
from fleet.modules.records import GUIDANCE_FILES, guidance_brief
from fleet.projections.project import project_status
from dataclasses import asdict
from pathlib import Path
import json


def guide(records, work_item: str | None, payload: dict) -> tuple[dict, dict | None]:
    """A dispatch payload whose first step opens with the guidance paragraph, and the constitution and charter
    versions for the action to pin; unchanged with None when the work has no recorded guidance."""
    guidance = None if work_item is None else records.dispatch_guidance(work_item)
    if guidance is None:
        return payload, None
    if not payload.get('steps'):
        raise ValueError('dispatch payload needs steps')
    taken = sorted({Path(path).name for path in payload.get('context') or []} & set(GUIDANCE_FILES.values()))
    if taken:
        raise ValueError(f"context already has {', '.join(taken)}, which guidance attaches")
    first, *rest = payload['steps']
    steps = [dict(first, prompt=f"{guidance_brief(guidance, work_item)}\n\n{first['prompt']}"), *rest]
    return dict(payload, steps=steps), guidance


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
            guided, guidance = guide(services.records, activation.work_item, payload['payload'])
            return services.execution.dispatch(activation.work_item, **context, **dict(payload, payload=guided),
                                               guidance=guidance)
        if command in ('summary', 'decide'):
            run = services.execution.activation_run(activation.id, activation.id)
            if command == 'summary':
                return services.work.set_summary(activation.work_item, **context, source_run=run.id,
                                                 authoring_role=activation.role, **payload)
            return services.decisions.record(activation.work_item, **context, source_run=run.id, **payload)
        raise ValueError('unknown orchestrator command')
