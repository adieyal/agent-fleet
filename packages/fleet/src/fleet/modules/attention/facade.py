"""Public attention commands and read-only queries."""

from datetime import datetime, timezone
from shlex import join
from typing import Callable

from .application import Commands
from .application.ports import AttentionRepository
from .application.observations import HostObservation, ingest_attention
from .application.input_observations import InputObservation, JobLink, close_refusals, ingest_input
from .domain import AttentionItem, PageAnnotation, ItemResolved, OWNERS, Refusal, StreamContext, STATES
from .domain.routing import RoutingHistory, route
from fleet.modules.records import TriageMandate


class AttentionFacade:
    def __init__(self, repository: AttentionRepository, clock: Callable[[], datetime], *,
                 mandate: Callable[[str], TriageMandate | None] | None = None,
                 routing_history: Callable[[StreamContext], RoutingHistory] | None = None,
                 job_link: JobLink | None = None) -> None:
        self.repository = repository
        self.clock = clock
        self.commands = Commands(repository, clock)
        self.mandate = mandate
        self.routing_history = routing_history
        self.job_link = job_link

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
                   owner_reason: str | None = None, page_annotation: PageAnnotation | None = None) -> AttentionItem:
        """Raise an item for its owner (agent or user), about its subject; seen again, it keeps its owner."""
        return self.commands.raise_item(project=project, kind=kind, owner=owner, source=source,
                                        source_reference=source_reference, headline=headline,
                                        context_reference=context_reference, actor=actor,
                                        work_item=work_item, run=run, stream_context=stream_context,
                                        reopen=reopen, options=options, subject=subject,
                                        owner_reason=owner_reason, page_annotation=page_annotation)

    def delegate(self, item_id: str, *, actor: str, note: str | None = None) -> AttentionItem:
        """Hand a user's item to the agent; it stays open and listed as the agent's."""
        self.require_delegable(item_id)
        return self.commands.hand_over(item_id, "agent", actor, reason=note, expected="user")

    def require_delegable(self, item_id: str) -> None:
        """Shared CLI/deck guard; checking availability grants no authority."""
        item = self.get(item_id)
        # Preserve lifecycle/session validation before checking the project's mandate.
        if item.state == "resolved":
            raise ItemResolved("attention item is resolved")
        if item.at_terminal or item.questions or (item.subject or '').startswith('session:'):
            raise ValueError("a session's question is answered only at its terminal, so an agent cannot take it")
        if item.owner == 'agent':
            raise ValueError("attention item is with the agent, not yours")
        if self.mandate is None or self.mandate(item.project) is None:
            raise ValueError("This project has no confirmed triage mandate; delegation is unavailable. "
                             "Inspect with fleet triage policy show PROJECT; record a policy before delegating.")

    def take(self, item_id: str, *, actor: str, reason: str | None = None) -> AttentionItem:
        """Take an item back from the agent; the agent may no longer act on it."""
        return self.commands.hand_over(item_id, "user", actor, reason=reason, expected="agent")

    def escalate(self, item_id: str, *, actor: str, reason: str) -> AttentionItem:
        """An agent hands its item to the user, saying why the user is needed."""
        if reason is None or not reason.strip():
            raise ValueError("an escalation needs a reason: why the user must decide")
        return self.commands.hand_over(item_id, "user", actor, reason=reason, expected="agent")

    def reply(self, item_id: str, body: str, *, actor: str) -> AttentionItem:
        """Append a message without changing lifecycle, ownership or recording a decision."""
        return self.commands.reply(item_id, body, actor)

    def acknowledge(self, item_id: str, *, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "acknowledged", actor)

    def observe_input(self, host: str, observation: InputObservation, *, project_id: str | None = None) -> None:
        link = (self.job_link(host, observation.job_id)
                if self.job_link is not None and observation.owner_type == "job" and observation.job_id else None)
        ingest_input(self.repository, host, observation, project_id, router=self.route,
                     run=link[0] if link else None, work_item=link[1] if link else None)

    def close_refusals(self, host: HostObservation, *, complete: bool) -> bool:
        """Repair job links and resolve refusals the job has finished or moved past."""
        return close_refusals(self.repository, host, complete=complete, now=self.clock(), job_link=self.job_link)

    def dismiss_refusals(self, item_id: str, *, actor: str) -> AttentionItem:
        item = self.repository.get(item_id)
        if not item.refusals:
            raise ValueError("only a job step's permission refusals can be dismissed")
        if item.state == "resolved":
            raise ItemResolved("attention item is resolved")
        return self.commands.change(item_id, "resolved", actor,
                                    details="dismissed; the job's permissions are unchanged")

    def observe(self, host: HostObservation, *, subjects: set[str] | None = None,
                raise_items: bool = True, deleted_jobs: set[str] = frozenset()) -> bool:
        """Ingest observations and report whether any cleared items were resolved."""
        return ingest_attention(self, host, subjects=subjects, raise_items=raise_items, deleted_jobs=deleted_jobs)

    def reopen(self, item_id: str, *, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "open", actor)

    def reopen_for_escalation(self, item_id: str, *, actor: str) -> AttentionItem:
        """Reopen a resolved agent item after Decisions authorized escalation of its partial action."""
        return self.commands.reopen_for_escalation(item_id, actor)

    def reconcile(self, source: str, references: set[str], *, actor: str,
                  subjects: set[str] | None = None, present_jobs: set[str] | None = None,
                  deleted_jobs: set[str] = frozenset()) -> bool:
        """Resolve cleared occurrences after a reachable source reports its current state."""
        return self.commands.reconcile(source, references, actor=actor, subjects=subjects,
                                       present_jobs=present_jobs, deleted_jobs=deleted_jobs)

    def snooze(self, item_id: str, *, until: datetime, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "snoozed", actor, until=until)

    def resolve(self, item_id: str, *, details: str, actor: str) -> AttentionItem:
        return self.commands.change(item_id, "resolved", actor, details=details)

    def resolved_answer_error(self, item: AttentionItem, answer: str, actor: str) -> ItemResolved:
        """Explain a refused answer using recorded resolution facts, with a follow-up command."""
        resolver = self.repository.resolving_actor(item.id)
        who = resolver if resolver else "actor not recorded"
        when = (item.resolved_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                if item.resolved_at is not None else "time not recorded")
        details = item.resolution_details or "details not recorded"
        message = f"attention item {item.id[:8]} was resolved by {who} at {when}: {details}"
        if item.work_item is None:
            message += "\nCannot suggest a follow-up decision: work item not recorded."
        else:
            command = join(["fleet", "decision", "record", "--work-item", item.work_item,
                            "--question", item.headline, "--answer", answer,
                            "--principle", "Follow-up to resolved attention item", "--actor", actor,
                            "--context", f"attention:{item.id}"])
            message += "\nRecord this answer as a new decision on the same work item:\n" + command
        return ItemResolved(message)

    def get(self, item_id: str) -> AttentionItem:
        return self.repository.get(item_id).effective(self.clock())

    def list(self, *, project: str | None = None, state: str | None = None,
             owner: str | None = None) -> list[AttentionItem]:
        if state is not None and state not in STATES:
            raise ValueError(f"unknown attention state: {state}")
        if owner is not None and owner not in OWNERS:
            raise ValueError(f"owner must be agent or user, not {owner!r}")
        now = self.clock()
        items = [item.effective(now) for item in self.repository.list(project=project, owner=owner)]
        return [item for item in items if (project is None or item.project == project)
                and (state is None or item.state == state) and (owner is None or item.owner == owner)]

    def next_snooze_after(self, after: float) -> float | None:
        """Next stored snooze deadline after the last wake, without hydrating items."""
        return min((end.timestamp() for end in self.repository.snooze_ends()
                    if end.timestamp() > after), default=None)

    def resolve_undoable(self, item_id: str, *, actor: str) -> tuple[AttentionItem, AttentionItem]:
        return self.commands.resolve_undoable(item_id, actor)

    def undo_resolution(self, previous: AttentionItem, resolved: AttentionItem, *, actor: str) -> None:
        self.commands.undo_resolution(previous, resolved, actor)
