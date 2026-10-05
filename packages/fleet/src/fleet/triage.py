"""Activation-bound triage controls. Scheduling is deliberately a separate controller step."""

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from fleet.modules.attention import AttentionItem
from fleet.modules.authority import Activation, AuthorityRejected
from fleet.modules.decisions import Decision
from fleet.modules.records import TriageMandate

if TYPE_CHECKING:
    from fleet.services.facades import Facades


FIELDS = {
    'retry': ('item', 'reason'),
    'add_step': ('item', 'prompt', 'title'),
    'grant': ('item', 'scope'),
    'resolve': ('item', 'details', 'principle'),
    'escalate': ('item', 'reason', 'principle'),
    'record_decision': ('item', 'question', 'answer', 'context', 'principle'),
}
AUTHORITY = {'resolve': 'resolve_attention', 'decide': 'record_decision'}


def triage_prompt(activation: Activation, mandate: TriageMandate, items: list[str]) -> str:
    return f'''Act as the triage agent for project {activation.project}.
Activation: {activation.id}. Pinned mandate version: {activation.mandate_version}.
Mandate: {json.dumps(asdict(mandate))}
Queued attention item IDs: {json.dumps(items)}
Read state first. Use only fleet control {activation.id} COMMAND 'JSON' for controller writes.
Read each item's context and governing constitution/epic charter before acting.
Commands and JSON fields:
state: {{}} (returns this project's unresolved agent-owned items with their context)
retry: item, reason (optional principle). Failed/lost jobs only; queues a stored retry
or submits one legacy host retry. A rejected or unconfirmed legacy send must not be repeated.
add_step: item, prompt, title (optional principle). Failed/blocked jobs only;
a blocked job's added step answers the observed blocked step.
grant: item, scope="refused" (optional principle). Every current refusal must be
allowed by this pinned mandate. No deny override, no all-Bash grants.
resolve: item, details, principle
escalate: item, reason, principle (nonempty reason saying why the user is needed)
record_decision: item, question, answer, context, principle
Each accepted command records an item-linked Decision with this activation and mandate.
Retry records a request, not proof that the job succeeded. Handling an alert does not complete its job.
You may act only on agent-owned items in this project. A user take-back stops later commands.
Do not dispatch jobs, update work criteria, delegate attention, or change the mandate.
Use escalate for decisions outside the mandate and for your own triage run's problems.
Finish after this bounded queue. A run finishing does not complete a work item.
'''


class TriageCommands:
    def __init__(self, services: 'Facades', activation: Activation) -> None:
        self.services, self.activation = services, activation

    def _source_run(self) -> str:
        actions = {action.id for action in self.services.execution.actions()
                   if action.activation == self.activation.id}
        runs = [run for run in self.services.execution.runs() if run.action in actions]
        if len(runs) != 1:
            raise AuthorityRejected('triage activation requires exactly one source run')
        return runs[0].id

    def _record(self, item: AttentionItem, command: str, payload: dict, answer: str, *,
                effect: str | None = None, completed_item: AttentionItem | None = None,
                retry_run: str | None = None) -> Decision:
        return self.services.decisions.record_attention(item.id,
            actor=self.activation.actor, activation=self.activation.id, source_run=self._source_run(),
            command=AUTHORITY.get(command, command), answer=answer,
            principle=payload.get('principle', f'triage mandate {self.activation.mandate_version}: {command}'),
            context=json.dumps(dict(command=AUTHORITY.get(command, command), project=item.project, subject=item.subject,
                                    step=None if item.stream_context is None else item.stream_context.step,
                                    request=payload, context_reference=item.context_reference)),
            question=payload.get('question'), effect=effect, completed_item=completed_item, retry_run=retry_run)

    def _reject_triage_subject(self, item: AttentionItem) -> None:
        context = item.stream_context
        if context is None or context.owner_type != 'job':
            return
        run = self.services.execution.find_run(context.host, context.owner_id)
        if run is None:
            return
        action = self.services.execution.get_action(run.action)
        if action.activation is not None and self.services.authority.get(action.activation).role == 'triage':
            raise AuthorityRejected("a triage run's problems must be escalated to the user")

    def execute(self, command: str, payload: dict) -> dict:
        result = self._execute(command, payload)
        if 'decision' in result:
            identity = result['decision']['id']
            intent = next(intent for intent in self.services.records.intents() if intent['key'] == identity)
            result['publication'] = intent['state']
            result['message'] = ('recorded; publication pending' if intent['state'] == 'pending'
                                 else 'recorded; publication ' + intent['state'])
        return result

    def _execute(self, command: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError('control payload must be a JSON object')
        if command == 'state':
            if payload:
                raise ValueError('state takes an empty JSON object')
            return dict(activation=asdict(self.activation),
                        mandate=asdict(self.services.authority.triage_mandate(self.activation.id)),
                        items=[asdict(item) for item in self.services.attention.list(project=self.activation.project,
                                                                                   owner='agent')
                               if item.state != 'resolved'])
        if command == 'decide':
            command = 'record_decision'
        if command not in FIELDS:
            raise AuthorityRejected(f'triage command must be one of: state, {", ".join(FIELDS)}')
        required = FIELDS[command]
        if set(payload) - set(required) - {'principle'} or set(required) - set(payload):
            raise ValueError(f'{command} fields: {", ".join(required)}; optional principle for retry/add_step/grant')
        for name, value in payload.items():
            if not isinstance(value, str) or (name != 'context' and not value.strip()):
                raise ValueError(f'{name} must be nonempty text')
        item = self.services.attention.get(payload['item'])
        authorized_item = (self.services.decisions.escalation_snapshot(item, self.activation.id)
                           if command == 'escalate' else item)
        self.services.authority.require_triage(AUTHORITY.get(command, command), authorized_item,
                                               actor=self.activation.actor, activation=self.activation.id)
        self._source_run()  # Reject undelivered/malformed activations before any external effect.
        if command not in ('escalate', 'record_decision'):
            self._reject_triage_subject(item)
        if command == 'retry':
            context = item.stream_context
            if context is None or context.owner_type != 'job' or context.source not in (
                    'job status failed', 'job status lost'):
                raise AuthorityRejected('retry requires a failed or lost job item')
            target = self.services.execution.find_run(context.host, context.owner_id)
            if target is not None:
                decision = self._record(item, command, payload, payload['reason'], effect='resolve', retry_run=target.id)
                result = json.loads(decision.context)
                return dict(decision=asdict(decision), run=result['run'], created=result['created'])
            # Legacy fleetd retry is not keyed. Persist its request first, and never send it twice.
            decision = self._record(item, command, payload, f'legacy retry requested: {payload["reason"]}')
            details = self.services.execution.retry_triage_job(item.id, actor=self.activation.actor,
                                                               activation=self.activation.id)
            current = self.services.attention.get(item.id)
            if current.owner == 'agent' and current.state != 'resolved':
                self.services.attention.resolve(item.id, details=f'{details}; decision:{decision.id}',
                                                actor=self.activation.actor)
            return dict(decision=asdict(decision), details=details)
        if command in ('grant', 'add_step'):
            decisions: list[Decision] = []

            def complete(snapshot: AttentionItem, details: str) -> None:
                decisions.append(self._record(snapshot, command, payload, details, effect='resolve', completed_item=snapshot))

            if command == 'grant':
                details = self.services.execution.grant_permissions(item.id, payload['scope'],
                    actor=self.activation.actor, activation=self.activation.id, complete=complete)
            else:
                details = self.services.execution.add_triage_step(item.id, payload['prompt'], payload['title'],
                    actor=self.activation.actor, activation=self.activation.id, complete=complete)
            return dict(decision=asdict(decisions[0]), details=details)
        answer = payload['details'] if command == 'resolve' else (
            f'escalated to the user: {payload["reason"]}' if command == 'escalate' else payload['answer'])
        decision = self._record(item, command, payload, answer,
                                effect='resolve' if command == 'resolve' else 'escalate' if command == 'escalate' else None)
        return dict(decision=asdict(decision))
