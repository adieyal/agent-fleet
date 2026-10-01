"""Public attention commands and read-only queries."""

from datetime import datetime
from typing import Callable

from .application import Commands
from .application.ports import AttentionRepository
from .application.observations import HostObservation, ingest_attention
from .application.input_observations import InputObservation, close_refusals, ingest_input
from .domain import AttentionItem, ItemResolved, OWNERS, Refusal, StreamContext, STATES
from .domain.routing import RoutingHistory, route
from fleet.modules.records import TriageMandate


class AttentionFacade:
    def __init__(self, repository: AttentionRepository, clock: Callable[[], datetime], *,
                 mandate: Callable[[str], TriageMandate | None] | None = None,
                 routing_history: Callable[[StreamContext], RoutingHistory] | None = None) -> None:
        self.repository = repository
        self.clock = clock
        self.commands = Commands(repository, clock)
        self.mandate = mandate
        self.routing_history = routing_history

    def route(self, project: str, kind: str, context: StreamContext | None, *,
              refusals: tuple[Refusal, ...] = ()) -> tuple[str, str | None]:
        mandate = None if self.mandate is None else self.mandate(project)
        history = (RoutingHistory() if self.routing_history is None or context is None
                   else self.routing_history(context))
        return route(kind, context, mandate, history, refusals=refusals)

    def raise_item(self, *, project: str, kind: str, owner: str, source: str, source_reference: str,
                   headline: str, context_reference: str, actor: str,
                   work_item: str | None = None, run: str | None = None,
                   stream_context: StreamContext | None = None, reopen: bool = False,
                   options: tuple[str, ...] = (), subject: str | None = None,
                   owner_reason: str | None = None) -> AttentionItem:
        """Raise an item for its owner (agent or user), about its subject; seen again, it keeps its owner."""
        return self.commands.raise_item(project=project, kind=kind, owner=owner, source=source,
                                        source_reference=source_reference, headline=headline,
                                        context_reference=context_reference, actor=actor,
                                        work_item=work_item, run=run, stream_context=stream_context,
                                        reopen=reopen, options=options, subject=subject,
                                        owner_reason=owner_reason)

    def delegate(self, item_id: str, *, actor: str, note: str | None = None) -> AttentionItem:
        """Hand a user's item to the agent; it stays open and listed as the agent's."""
        return self.commands.hand_over(item_id, "agent", actor, reason=note, expected="user")

    def take(self, item_id: str, *, actor: str, reason: str | None = None) -> AttentionItem:
        """Take an item back from the agent; the agent may no longer act on it."""
        return self.commands.hand_over(item_id, "user", actor, reason=reason, expected="agent")

    def escalate(self, item_id: str, *, actor: str, reason: str) -> AttentionItem:
        """An agent hands its item to the user, saying why the user is needed."""
        if reason is None or not reason.strip():
            raise ValueError("an escalation needs a reason: why the user must decide")
        return self.commands.hand_over(item_id, "user", actor, reason=reason, expected="agent")

    def acknowledge(self, item_id: str, *, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "acknowledged", actor)

    def observe_input(self, host: str, observation: InputObservation, *, project_id: str | None = None) -> None:
        ingest_input(self.repository, host, observation, project_id, router=self.route)

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

    def observe(self, host: HostObservation, *, subjects: set[str] | None = None,
                raise_items: bool = True) -> bool:
        """Ingest observations and report whether any cleared items were resolved."""
        return ingest_attention(self, host, subjects=subjects, raise_items=raise_items)

    def reopen(self, item_id: str, *, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "open", actor)

    def reconcile(self, source: str, references: set[str], *, actor: str,
                  subjects: set[str] | None = None) -> bool:
        """Resolve cleared occurrences after a reachable source reports its current state."""
        return self.commands.reconcile(source, references, actor=actor, subjects=subjects)

    def snooze(self, item_id: str, *, until: datetime, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "snoozed", actor, until=until)

    def resolve(self, item_id: str, *, details: str, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "resolved", actor, details=details)

    def get(self, item_id: str) -> AttentionItem:
        return self.repository.get(item_id).effective(self.clock())

    def list(self, *, project: str | None = None, state: str | None = None,
             owner: str | None = None) -> list[AttentionItem]:
        if state is not None and state not in STATES:
            raise ValueError(f"unknown attention state: {state}")
        if owner is not None and owner not in OWNERS:
            raise ValueError(f"owner must be agent or user, not {owner!r}")
        now = self.clock()
        items = [item.effective(now) for item in self.repository.list()]
        return [item for item in items if (project is None or item.project == project)
                and (state is None or item.state == state) and (owner is None or item.owner == owner)]
