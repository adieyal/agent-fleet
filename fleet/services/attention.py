"""Attention commands and blocked answer routing."""
from __future__ import annotations

from fleet.transport import FleetError


class AttentionCommands:
    def __init__(self, services, workspace, attention, references):
        self.services, self.workspace, self.attention, self.references = services, workspace, attention, references

    def answer(self, reference: str, answer: str, *, actor: str, next_step: str | None):
        identity = self.references.attention_item(reference)
        try:
            context = self.attention().get(identity).stream_context
            if context is not None and context.blocked_step:
                if next_step is not None:
                    raise ValueError("a blocked job step's answer has no --next-step; use fleet work set")
                details = self.services.execution.answer_blocked(identity, answer, actor=actor)
                return {"id": identity, "resolution": details}
            return self.services.decisions.answer(identity, answer, actor=actor, next_step=next_step)
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error

    def execute(self, command: str, data: dict):
        data = data.copy()
        if command in ('add', 'list') and data['project'] is not None:
            data['project'] = self.workspace().resolve_project(data['project'])
        try:
            attention = self.attention()
            if command not in ('add', 'list'):
                data['id'] = self.references.attention_item(data['id'])
            if command == 'add' and data['work_item'] is not None:
                data['work_item'] = self.references.work(data['work_item'])
            if command == 'add':
                data['owner_reason'] = data.pop('reason')
                return attention.raise_item(**data)
            if command == 'list':
                all_items = data.pop('all')
                items = attention.list(**data)
                return [item for item in items if item.state != 'resolved'] if data['state'] is None and not all_items else items
            identity = data.pop('id')
            operation = {'ack': 'acknowledge'}.get(command, command)
            fields = {
                'delegate': ('actor', 'note'), 'take': ('actor', 'reason'),
                'escalate': ('actor', 'reason'), 'ack': ('actor',),
                'snooze': ('actor', 'until'), 'resolve': ('actor', 'details'),
            }[command]
            return getattr(attention, operation)(identity, **{name: data[name] for name in fields})
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error
