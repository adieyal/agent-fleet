"""Public attention commands and read-only queries."""

from datetime import datetime
from typing import Callable

from .application import Commands
from .application.ports import AttentionRepository
from .domain import AttentionItem, STATES


class AttentionFacade:
    def __init__(self, repository: AttentionRepository, clock: Callable[[], datetime]) -> None:
        self.repository = repository
        self.clock = clock
        self.commands = Commands(repository, clock)

    def raise_item(self, *, project: str, kind: str, owner: str, source: str, source_reference: str,
                   headline: str, context_reference: str, actor: str,
                   work_item: str | None = None, run: str | None = None, reopen: bool = False) -> AttentionItem:
        return self.commands.raise_item(project=project, kind=kind, owner=owner, source=source,
                                        source_reference=source_reference, headline=headline,
                                        context_reference=context_reference, actor=actor,
                                        work_item=work_item, run=run, reopen=reopen)

    def acknowledge(self, item_id: str, *, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "acknowledged", actor)

    def snooze(self, item_id: str, *, until: datetime, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "snoozed", actor, until=until)

    def resolve(self, item_id: str, *, details: str, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "resolved", actor, details=details)

    def get(self, item_id: str) -> AttentionItem:
        return self.repository.get(item_id).effective(self.clock())

    def list(self, *, project: str | None = None, state: str | None = None) -> list[AttentionItem]:
        if state is not None and state not in STATES:
            raise ValueError(f"unknown attention state: {state}")
        now = self.clock()
        items = [item.effective(now) for item in self.repository.list()]
        return [item for item in items if (project is None or item.project == project)
                and (state is None or item.state == state)]
