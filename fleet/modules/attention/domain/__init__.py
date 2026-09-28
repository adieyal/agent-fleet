"""Attention records and state rules."""

from dataclasses import dataclass, replace
from datetime import datetime

STATES = ("open", "acknowledged", "snoozed", "resolved")
KINDS = ("decision", "blocker", "alert")


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


@dataclass(frozen=True)
class AttentionItem:
    id: str
    project: str
    work_item: str | None
    run: str | None
    kind: str
    owner: str
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

    def __post_init__(self) -> None:
        for option in self.options:
            required(option, "option")
        for name in ("project", "owner", "source", "source_reference", "headline", "context_reference"):
            required(getattr(self, name), name)
        if len(self.headline.split()) > 12:
            raise ValueError("headline must contain 12 words or fewer")
        if self.kind not in KINDS:
            raise ValueError("kind must be decision, blocker or alert")
        if self.state not in STATES:
            raise ValueError(f"unknown attention state: {self.state}")

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
