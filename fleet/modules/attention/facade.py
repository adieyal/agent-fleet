"""Public attention commands and read-only queries."""

from datetime import datetime
from typing import Callable

from .application import Commands
from .application.ports import AttentionRepository
from .application.observations import HostObservation, ingest_attention
from .application.input_observations import InputObservation, close_refusals, ingest_input
from .domain import AttentionItem, ItemResolved, StreamContext, STATES


class AttentionFacade:
    def __init__(self, repository: AttentionRepository, clock: Callable[[], datetime]) -> None:
        self.repository = repository
        self.clock = clock
        self.commands = Commands(repository, clock)

    def raise_item(self, *, project: str, kind: str, owner: str, source: str, source_reference: str,
                   headline: str, context_reference: str, actor: str,
                   work_item: str | None = None, run: str | None = None,
                   stream_context: StreamContext | None = None, reopen: bool = False,
                   options: tuple[str, ...] = ()) -> AttentionItem:
        return self.commands.raise_item(project=project, kind=kind, owner=owner, source=source,
                                        source_reference=source_reference, headline=headline,
                                        context_reference=context_reference, actor=actor,
                                        work_item=work_item, run=run, stream_context=stream_context,
                                        reopen=reopen, options=options)

    def acknowledge(self, item_id: str, *, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "acknowledged", actor)

    def observe_input(self, host: str, observation: InputObservation, *, project_id: str | None = None) -> None:
        ingest_input(self.repository, host, observation, project_id)

    def close_refusals(self, host: HostObservation, *, complete: bool) -> bool:
        """Resolve job refusal batches whose step has ended; report whether any were."""
        return close_refusals(self.repository, host, complete=complete, now=self.clock())

    def dismiss_refusals(self, item_id: str, *, actor: str) -> AttentionItem:
        item = self.repository.get(item_id)
        if not item.refusals:
            raise ValueError("only a job step's permission refusals can be dismissed")
        if item.state == "resolved":
            raise ItemResolved("attention item is resolved")
        return self.commands.change(item_id, "resolved", actor,
                                    details="dismissed; the job's permissions are unchanged")

    def observe(self, host: HostObservation, *, owners: set[str] | None = None,
                raise_items: bool = True) -> bool:
        """Ingest observations and report whether any cleared items were resolved."""
        return ingest_attention(self, host, owners=owners, raise_items=raise_items)

    def reopen(self, item_id: str, *, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "open", actor)

    def reconcile(self, source: str, references: set[str], *, actor: str,
                  owners: set[str] | None = None) -> bool:
        """Resolve cleared occurrences after a reachable source reports its current state."""
        return self.commands.reconcile(source, references, actor=actor, owners=owners)

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

    def resolve_undoable(self, item_id: str, *, actor: str) -> tuple[AttentionItem, AttentionItem]:
        return self.commands.resolve_undoable(item_id, actor)

    def undo_resolution(self, previous: AttentionItem, resolved: AttentionItem, *, actor: str) -> None:
        self.commands.undo_resolution(previous, resolved, actor)
