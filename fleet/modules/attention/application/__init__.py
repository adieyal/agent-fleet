"""Attention command workflows."""

from dataclasses import replace
from datetime import datetime
from typing import Callable
from uuid import uuid4

from .ports import AttentionRepository
from ..domain import AttentionItem, StreamContext, required


class Commands:
    def __init__(self, repository: AttentionRepository, clock: Callable[[], datetime]) -> None:
        self.repository = repository
        self.clock = clock

    def raise_item(self, *, project: str, kind: str, owner: str, source: str, source_reference: str,
                   headline: str, context_reference: str, actor: str,
                   work_item: str | None = None, run: str | None = None,
                   stream_context: StreamContext | None = None, reopen: bool = False,
                   options: tuple[str, ...] = (), subject: str | None = None,
                   owner_reason: str | None = None) -> AttentionItem:
        required(actor, "actor")
        now = self.clock()
        with self.repository.transaction() as repository:
            previous = repository.find(source, source_reference)
            # Seen again, an item keeps the owner it was handed to; the owner given applies to a new item.
            handed = (dict(owner=previous.owner, owner_reason=previous.owner_reason, owner_actor=previous.owner_actor,
                           owner_at=previous.owner_at) if previous else dict(owner=owner, owner_reason=owner_reason))
            item = AttentionItem(
                id=previous.id if previous else str(uuid4()), project=project, kind=kind, subject=subject, **handed,
                source=source, source_reference=source_reference, headline=headline,
                context_reference=context_reference, work_item=work_item, run=run,
                state=previous.state if previous else "open",
                snooze_until=previous.snooze_until if previous else None,
                resolution_details=previous.resolution_details if previous else None, last_seen=now,
                acknowledged_at=previous.acknowledged_at if previous else None,
                resolved_at=previous.resolved_at if previous else None, stream_context=stream_context,
                options=tuple(options))
            if reopen and previous is not None and previous.state == "resolved":
                item = replace(item, state="open", snooze_until=None, resolution_details=None,
                               resolved_at=None, acknowledged_at=None)
            if previous is None:
                action = repository.imported_action(source_reference)
                if action is not None:
                    item = replace(item, state=action.state, snooze_until=action.until,
                                   acknowledged_at=action.at if action.state == "acknowledged" else None)
            repository.save(item, previous.state if previous else None, actor)
        return item.effective(now)

    def reconcile(self, source: str, references: set[str], *, actor: str,
                  subjects: set[str] | None = None) -> bool:
        changed = False
        for item in self.repository.list():
            if (item.source == source and item.source_reference not in references
                    and item.state != "resolved" and (subjects is None or item.subject in subjects)):
                details = ("answered in session or session removed" if item.kind == "decision"
                           else "job retried, finished or removed")
                self.change(item.id, "resolved", actor, details=details)
                changed = True
        return changed

    def hand_over(self, item_id: str, owner: str, actor: str, *, reason: str | None,
                  expected: str) -> AttentionItem:
        """Give an item owned by `expected` to `owner`, recording who did it and why."""
        now = self.clock()
        with self.repository.transaction() as repository:
            previous = repository.get(item_id)
            if previous.owner != expected:
                raise ValueError(f"attention item is {'with the agent' if previous.owner == 'agent' else 'yours'}, "
                                 f"not {'the agent' if expected == 'agent' else 'yours'}")
            item = previous.hand_over(owner, now, actor, reason=reason)
            repository.save_owner(item, previous.owner, actor)
        return item.effective(now)

    def change(self, item_id: str, state: str, actor: str, *, until: datetime | None = None,
               details: str | None = None) -> AttentionItem:
        required(actor, "actor")
        now = self.clock()
        with self.repository.transaction() as repository:
            previous = repository.get(item_id)
            item = previous.transition(state, now, until=until, details=details)
            repository.save(item, previous.state, actor)
        return item.effective(now)
