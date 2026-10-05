"""Attention records and state rules."""

from dataclasses import dataclass, replace
from datetime import datetime

STATES = ("open", "acknowledged", "snoozed", "resolved")
KINDS = ("decision", "blocker", "alert")
OWNERS = ("agent", "user")


class ItemResolved(ValueError):
    """A resolved attention item cannot be acted on."""


def required(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class ImportedAction:
    state: str
    at: datetime
    until: datetime | None


@dataclass(frozen=True)
class StreamContext:
    host: str
    owner_type: str
    owner_id: str
    project: str
    project_id: str | None
    source: str
    summary: str
    since: float | None
    step: int | None = None  # the job step a permission batch belongs to, or that is blocked
    cwd: str | None = None   # where the session runs, from its hook
    message: str | None = None  # a blocked step's final message, its question; None when fleetd reported none

    @property
    def blocked_step(self) -> bool:
        """A job step that ended asking its supervisor, answered by adding a step to the job."""
        return self.owner_type == "job" and self.source == BLOCKED_SOURCE and self.step is not None


BLOCKED_SOURCE = "job status blocked"


@dataclass(frozen=True)
class QuestionOption:
    label: str
    description: str


@dataclass(frozen=True)
class Question:
    """A question an interactive session put to the person at its terminal (AskUserQuestion)."""
    header: str
    question: str
    options: tuple[QuestionOption, ...]
    multi_select: bool = False


@dataclass(frozen=True)
class Refusal:
    """One permission request a non-interactive job was refused, and the rules that would allow it."""
    occurrence: str
    tool: str
    description: str
    detail: str
    rules: tuple[str, ...] | None  # None when the worker's fleetd proposes no rules
    observed_at: float
    denied_by: tuple[str, ...] = ()  # deny rules (with their settings file) no allow rule can override


def refusal_rules(refusals: tuple[Refusal, ...]) -> list[str] | None:
    """The distinct rules that would allow these requests; None when the worker proposed none.

    A request a deny rule refuses contributes none: allowing it for the job would change nothing.
    """
    if any(refusal.rules is None for refusal in refusals):
        return None
    return list(dict.fromkeys(rule for refusal in refusals if not refusal.denied_by for rule in refusal.rules or ()))


@dataclass(frozen=True)
class PageAnnotation:
    comment_id: str
    page: str
    revision: str
    body: str
    selector: dict | list[dict]
    creator: str
    requested_owner: str
    reason: str
    headline: str
    parent: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        for name in ('comment_id', 'page', 'revision', 'body', 'creator', 'reason', 'headline'):
            required(getattr(self, name), name)
        if self.version != 1 or self.requested_owner not in OWNERS:
            raise ValueError('invalid page annotation version or owner')


@dataclass(frozen=True)
class AttentionItem:
    id: str
    project: str
    work_item: str | None
    run: str | None
    kind: str
    owner: str  # who must act next: agent or user
    source: str
    source_reference: str
    headline: str
    context_reference: str
    state: str
    snooze_until: datetime | None
    resolution_details: str | None
    last_seen: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    stream_context: StreamContext | None = None
    options: tuple[str, ...] = ()
    refusals: tuple[Refusal, ...] = ()
    questions: tuple[Question, ...] = ()  # answered in the session's terminal, never in Fleet
    subject: str | None = None  # what the item is about: job:<host>:<id>, session:<host>:<id> or run:<id>
    owner_reason: str | None = None  # why the item went to its owner, as given with the last hand-over
    owner_actor: str | None = None   # who made the last hand-over; None while the item has its first owner
    owner_at: datetime | None = None
    page_annotation: PageAnnotation | None = None

    def __post_init__(self) -> None:
        if self.page_annotation is not None and (self.source, self.kind, self.context_reference) != (
                'page', 'decision', self.page_annotation.page):
            raise ValueError('page annotations require a page decision and its canonical context')
        for option in self.options:
            required(option, "option")
        for name in ("project", "owner", "source", "source_reference", "headline", "context_reference"):
            required(getattr(self, name), name)
        if len(self.headline.split()) > 12:
            raise ValueError("headline must contain 12 words or fewer")
        if self.kind not in KINDS:
            raise ValueError("kind must be decision, blocker or alert")
        if self.owner not in OWNERS:
            raise ValueError(f"owner must be agent or user, not {self.owner!r}")
        if self.state not in STATES:
            raise ValueError(f"unknown attention state: {self.state}")

    @property
    def at_terminal(self) -> bool:
        """A session's item: only the person at that session's terminal can answer it."""
        return self.stream_context is not None and self.stream_context.owner_type == "session"

    def hand_over(self, owner: str, now: datetime, actor: str, *, reason: str | None) -> "AttentionItem":
        """The item with a new owner. An agent cannot act on a session's question, and a resolved item has no one
        left to act."""
        required(actor, "actor")
        if owner not in OWNERS:
            raise ValueError(f"owner must be agent or user, not {owner!r}")
        if self.state == "resolved":
            raise ItemResolved("attention item is resolved")
        if owner == self.owner:
            raise ValueError(f"attention item is already {'with the agent' if owner == 'agent' else 'yours'}")
        if owner == "agent" and self.at_terminal:
            raise ValueError("a session's question is answered only at its terminal, so an agent cannot take it")
        if reason is not None:
            required(reason, "reason")
        return replace(self, owner=owner, owner_reason=reason, owner_actor=actor, owner_at=now)

    def effective(self, now: datetime) -> "AttentionItem":
        if self.state == "snoozed" and self.snooze_until is not None and self.snooze_until <= now:
            return replace(self, state="open")
        return self

    def transition(self, state: str, now: datetime, *, until: datetime | None = None,
                   details: str | None = None) -> "AttentionItem":
        if state == "snoozed" and (until is None or until.tzinfo is None or until <= now):
            raise ValueError("snooze-until must be a timezone-aware time in the future")
        if state == "resolved":
            if details is None:
                raise ValueError("resolution details are required")
            required(details, "resolution details")
        elif self.state == "resolved":
            raise ItemResolved("attention item is resolved")
        return replace(self, state=state, snooze_until=until, resolution_details=details,
                       acknowledged_at=now if state == "acknowledged" else None,
                       resolved_at=now if state == "resolved" else None)
