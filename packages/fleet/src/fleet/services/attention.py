"""Attention commands and blocked answer routing."""
from __future__ import annotations
import time
from datetime import timedelta
from uuid import uuid4


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


class LiveAttention:
    def act_on_attention(self, action: str, item_id: str, seconds: float | None = None, undo: str | None = None) -> dict:
        result = {}
        if action == "delegate":
            self.attention.delegate(item_id, actor="web-user")
        elif action == "take":
            self.attention.take(item_id, actor="web-user")
        elif action == "acknowledge":
            self.attention.acknowledge(item_id, actor="web-user")
        elif action == "snooze":
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
                raise FleetError("snooze needs a positive number of seconds")
            self.attention.snooze(item_id, until=self.attention.clock() + timedelta(seconds=seconds), actor="web-user")
        elif action == "reopen":
            self.attention.reopen(item_id, actor="web-user")
        elif action == "resolve":
            with self.changed:
                now = time.monotonic()
                receipts = {key: value for key, value in getattr(self, "attention_undos", {}).items()
                            if value[0] > now}
                previous, resolved = self.attention.resolve_undoable(item_id, actor="web-user")
                token = str(uuid4())
                receipts[token] = (now + 6, previous, resolved)
                self.attention_undos = receipts
                result = {"undo": token, "undo_seconds": 6}
        elif action == "undo-resolve":
            with self.changed:
                receipts = getattr(self, "attention_undos", {})
                receipt = receipts.get(undo) if isinstance(undo, str) else None
                if receipt is None or receipt[0] <= time.monotonic() or receipt[1].id != item_id:
                    raise FleetError("Resolve Undo expired or is unavailable")
                self.attention.undo_resolution(receipt[1], receipt[2], actor="web-user")
                del receipts[undo]
        else:
            raise FleetError(f"unknown attention action '{action}'")
        self.bump()
        return result

    def answer_decision(self, item_id, answer):
        decision = self.container.decisions().answer(item_id, answer, actor='user')
        self.bump()
        return decision

    def refusal_action(self, action, item_id, scope=None):
        if action == 'allow':
            details = self.container.execution().grant_permissions(item_id, scope, actor='web-user')
        else:
            details = self.attention.dismiss_refusals(item_id, actor='web-user').resolution_details
        self.bump()
        return {'id': item_id, 'resolution': details}

    def answer_blocked(self, item_id, answer, work_item=None):
        details = self.container.execution().answer_blocked(item_id, answer, actor='web-user', work_item=work_item)
        self.bump()
        return {'id': item_id, 'resolution': details}
