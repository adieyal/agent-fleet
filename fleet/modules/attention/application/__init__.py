"""Attention command workflows."""

from dataclasses import replace
from datetime import datetime
from typing import Callable
from uuid import uuid4

from .ports import AttentionRepository
from ..domain import AttentionItem, required


class Commands:
    def __init__(self, repository: AttentionRepository, clock: Callable[[], datetime]) -> None:
        self.repository = repository
        self.clock = clock

    def raise_item(self, *, project: str, kind: str, owner: str, source: str, source_reference: str,
                   headline: str, context_reference: str, actor: str,
                   work_item: str | None = None, run: str | None = None, reopen: bool = False) -> AttentionItem:
        required(actor, "actor")
        now = self.clock()
        with self.repository.transaction() as repository:
            previous = repository.find(source, source_reference)
            item = AttentionItem(
                id=previous.id if previous else str(uuid4()), project=project, kind=kind, owner=owner,
                source=source, source_reference=source_reference, headline=headline,
                context_reference=context_reference, work_item=work_item, run=run,
                state=previous.state if previous else "open",
                snooze_until=previous.snooze_until if previous else None,
                resolution_details=previous.resolution_details if previous else None, last_seen=now)
            if reopen and previous is not None and previous.state == "resolved":
                item = replace(item, state="open", snooze_until=None, resolution_details=None)
            if previous is None:
                action = repository.imported_action(source_reference)
                if action is not None:
                    item = replace(item, state=action.state, snooze_until=action.until)
            repository.save(item, previous.state if previous else None, actor)
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
