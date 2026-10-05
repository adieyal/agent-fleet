"""Local orchestrator command delivery through controller facades."""

from fleet.modules.records import GUIDANCE_FILES, guidance_brief
from fleet.projections.decisions import decision_log, promotion, promotion_marker
from fleet.projections.project import project_status
from fleet.triage import TriageCommands, triage_prompt
from dataclasses import asdict
from pathlib import Path
import json


def guide(records, work_item: str | None, payload: dict) -> tuple[dict, dict | None]:
    """A dispatch payload whose first step opens with the guidance paragraph, and the constitution and charter
    versions for the action to pin; unchanged with None when the work has no recorded guidance."""
    from datetime import datetime, timezone
    from fleet.modules.execution import decision_applies
    source = getattr(records, 'decision_source', None)
    if source is not None and work_item is not None:
        boundary = datetime.now(timezone.utc)
        snapshot = source()
        decisions = [decision for decision in snapshot if decision_applies(records.work, work_item, decision)]
        payload = dict(payload, decisions_dispatch_at=boundary.isoformat(),
                       decisions_dispatch_ids=[decision.id for decision in snapshot])
        if decisions and payload.get('steps'):
            text = '\n\n'.join(f"Question: {d.question}\nAnswer: {d.answer}\nActor: {d.actor}\nPrinciple: {d.principle}" for d in decisions)
            first, *rest = payload['steps']
            payload['steps'] = [dict(first, prompt=f"## Decisions in force at dispatch\n\n{text}\n\n{first['prompt']}"), *rest]
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


def promote_decision(services, epic: str, decision: str, *, actor: str):
    """Add a decision on the epic's work, dated, to the epic charter's decisions in force as a new version."""
    project = services.work.get(epic).project
    entry = next((entry for entry in decision_log(services.work, services.decisions, project=project, epic=epic)
                  if entry["id"] == decision), None)
    if entry is None:
        raise LookupError(f"no decision {decision} on the work of epic {epic}")
    return services.records.promote(project, epic, promotion(entry), marker=promotion_marker(entry), actor=actor)


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
decide: question, answer, context, principle (optional: the rule relied on)
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
    def __init__(self, services, activation: str):
        self.services = services
        self.activation = self.services.authority.get(activation)

    def execute(self, command: str, payload: dict):
        services, activation = self.services, self.activation
        if activation.role == 'triage':
            return TriageCommands(services, activation).execute(command, payload)
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
