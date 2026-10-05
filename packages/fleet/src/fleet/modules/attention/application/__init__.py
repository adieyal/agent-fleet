"""Attention command workflows."""

from dataclasses import replace
from datetime import datetime
from typing import Callable
from uuid import uuid4

from .ports import AttentionRepository
from ..domain import AttentionReply, AttentionItem, PageAnnotation, ItemResolved, StreamContext, required


class Commands:
    def __init__(self, repository: AttentionRepository, clock: Callable[[], datetime]) -> None:
        self.repository = repository
        self.clock = clock

    def reply(self, item_id: str, body: str, actor: str) -> AttentionItem:
        if not isinstance(body, str) or not body.strip():
            raise ValueError('reply body is required')
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('actor is required')
        if len(body.encode()) > 8 * 1024:
            raise ValueError('reply exceeds 8 KiB')
        with self.repository.transaction() as repository:
            previous = repository.get(item_id)
            message = AttentionReply(str(uuid4()), body, actor, self.clock())
            item = replace(previous, replies=previous.replies + (message,))
            repository.save_reply(item, message)
            return item.effective(self.clock())

    def raise_item(self, *, project: str, kind: str, owner: str, source: str, source_reference: str,
                   headline: str, context_reference: str, actor: str,
                   work_item: str | None = None, run: str | None = None,
                   stream_context: StreamContext | None = None, reopen: bool = False,
                   options: tuple[str, ...] = (), subject: str | None = None,
                   owner_reason: str | None = None, page_annotation: PageAnnotation | None = None) -> AttentionItem:
        required(actor, "actor")
        now = self.clock()
        with self.repository.transaction() as repository:
            previous = repository.find(source, source_reference)
            if page_annotation is not None and previous is not None:
                if previous.page_annotation != page_annotation or previous.project != project:
                    raise ValueError('comment request payload changed')
                return previous.effective(now)
            if previous is not None and previous.page_annotation is not None:
                if (project, kind, headline, context_reference, work_item, run, stream_context) != (
                        previous.project, previous.kind, previous.headline, previous.context_reference,
                        previous.work_item, previous.run, previous.stream_context):
                    raise ValueError('page comment is immutable')
                return previous.effective(now)
            # Seen again, an item keeps the owner it was handed to; the owner given applies to a new item.
            handed = (dict(owner=previous.owner, owner_reason=previous.owner_reason, owner_actor=previous.owner_actor,
                           owner_at=previous.owner_at) if previous else dict(owner=owner, owner_reason=owner_reason,
                           owner_at=now, owner_actor=actor))
            item = AttentionItem(
                id=previous.id if previous else str(uuid4()), replies=previous.replies if previous else (),
                project=project, kind=kind, subject=subject, **handed,
                source=source, source_reference=source_reference, headline=headline,
                context_reference=context_reference, work_item=work_item, run=run,
                state=previous.state if previous else "open",
                snooze_until=previous.snooze_until if previous else None,
                resolution_details=previous.resolution_details if previous else None, last_seen=now,
                acknowledged_at=previous.acknowledged_at if previous else None,
                resolved_at=previous.resolved_at if previous else None, stream_context=stream_context,
                options=tuple(options), page_annotation=page_annotation if page_annotation is not None else
                    (previous.page_annotation if previous else None))
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
                  subjects: set[str] | None = None, present_jobs: set[str] | None = None,
                  deleted_jobs: set[str] = frozenset()) -> bool:
        """Resolve cleared items. With present_jobs, a job's item stays open while its job is absent
        (aged out of the stream, or not reported yet) unless the job is among deleted_jobs."""
        changed = False
        for item in self.repository.list():
            if (item.source == source and item.source_reference not in references
                    and item.state != "resolved" and (subjects is None or item.subject in subjects)):
                if item.subject in deleted_jobs:
                    details = "job deleted from its host"
                elif present_jobs is not None and (item.subject or "").startswith("job:") and item.subject not in present_jobs:
                    continue
                else:
                    details = ("answered in session or session removed" if item.kind == "decision"
                               else "job retried or finished")
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

    def reopen_for_escalation(self, item_id: str, actor: str) -> AttentionItem:
        required(actor, "actor")
        with self.repository.transaction() as repository:
            previous = repository.get(item_id)
            if previous.owner != "agent" or previous.state != "resolved":
                raise ValueError("escalation reopening requires a resolved agent-owned item")
            item = replace(previous, state="open", snooze_until=None, resolution_details=None,
                           acknowledged_at=None, resolved_at=None)
            repository.save(item, previous.state, actor)
        return item

    def change(self, item_id: str, state: str, actor: str, *, until: datetime | None = None,
               details: str | None = None) -> AttentionItem:
        required(actor, "actor")
        now = self.clock()
        with self.repository.transaction() as repository:
            previous = repository.get(item_id)
            item = previous.transition(state, now, until=until, details=details)
            repository.save(item, previous.state, actor)
        return item.effective(now)

    def resolve_undoable(self, item_id: str, actor: str) -> tuple[AttentionItem, AttentionItem]:
        """Capture the prior attention state in the same transaction as a manual resolution."""
        required(actor, "actor")
        now = self.clock()
        with self.repository.transaction() as repository:
            previous = repository.get(item_id).effective(now)
            if previous.state == "resolved":
                raise ItemResolved("attention item is resolved")
            resolved = previous.transition("resolved", now, details="resolved from the deck")
            repository.save(resolved, previous.state, actor)
        return previous, resolved

    def undo_resolution(self, previous: AttentionItem, resolved: AttentionItem, actor: str) -> None:
        """Restore only attention fields; never overwrite a newer resolution or host observation."""
        required(actor, "actor")
        with self.repository.transaction() as repository:
            current = repository.get(previous.id)
            if (current.state != "resolved" or current.resolved_at != resolved.resolved_at
                    or current.resolution_details != resolved.resolution_details):
                raise ItemResolved("attention changed after resolution; cannot undo")
            restored = replace(current, state=previous.state, snooze_until=previous.snooze_until,
                               acknowledged_at=previous.acknowledged_at, resolved_at=previous.resolved_at,
                               resolution_details=previous.resolution_details)
            repository.save(restored, current.state, actor)
