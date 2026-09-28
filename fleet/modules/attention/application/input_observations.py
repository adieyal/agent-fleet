"""Accept explicit runtime input transitions without inferring answers from silence."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4

from .ports import AttentionRepository
from ..domain import AttentionItem, StreamContext


@dataclass(frozen=True)
class InputObservation:
    schema_version: int
    runtime: str
    owner_type: str
    job_id: str | None
    session_id: str
    step_index: int | None
    project: str
    kind: str
    reason: str
    source_event: str
    source_event_id: str
    observed_at: float
    context_reference: str
    request: dict | None = None  # tool, description, detail; absent from older fleetd

    def question(self) -> tuple[str, str]:
        """The headline and context a person reads to answer the request."""
        if not self.request:
            return "Claude needs permission", self.context_reference
        parts = [self.request.get("description", ""), self.request.get("detail", "")]
        context = "\n\n".join(part for part in parts if part) or self.context_reference
        return f"Claude asks to use {self.request.get('tool') or 'a tool'}", context


def ingest_input(repository: AttentionRepository, host: str, observation: InputObservation,
                 project_id: str | None) -> None:
    if observation.schema_version != 1:
        raise ValueError("unsupported input observation schema version")
    if observation.runtime != "claude":
        raise ValueError("unsupported input observation runtime")
    if observation.owner_type not in ("job", "session"):
        raise ValueError("invalid input observation owner type")
    if (observation.kind, observation.source_event) not in (
            ("input_requested", "PermissionRequest"), ("input_cleared", "PostToolUse")):
        raise ValueError("unsupported input observation transition")
    owner_id = observation.job_id if observation.owner_type == "job" else observation.session_id
    if not owner_id or not observation.source_event_id:
        raise ValueError("input observation requires owner and occurrence identity")
    owner = f"{observation.owner_type}:{host}:{owner_id}"
    source = f"runtime-input:{host}"
    reference = f"{owner}:{observation.source_event_id}"
    seen = datetime.fromtimestamp(observation.observed_at, timezone.utc)
    with repository.transaction() as transaction:
        previous = transaction.find(source, reference)
        if previous is not None and (previous.state == "resolved" or previous.last_seen > seen):
            return
        headline, detail = observation.question()
        if previous is not None and observation.kind == "input_requested":
            if (previous.headline, previous.context_reference) == (headline, detail):
                return
            # Items recorded before fleetd sent the request text pick it up on replay.
            transaction.save(replace(previous, headline=headline, context_reference=detail),
                             previous.state, "runtime-hook")
            return
        context = StreamContext(host, observation.owner_type, owner_id, observation.project,
                                project_id, "Claude permission request", headline,
                                observation.observed_at)
        item = previous if previous is not None else AttentionItem(
            id=str(uuid4()), project=project_id if project_id is not None else observation.project,
            work_item=None, run=None, kind="decision", owner=owner, source=source,
            source_reference=reference, headline=headline,
            context_reference=detail, state="open", snooze_until=None,
            resolution_details=None, last_seen=seen, stream_context=context)
        if observation.kind == "input_cleared":
            item = replace(item.transition("resolved", seen, details="answered in session"), last_seen=seen)
        transaction.save(item, previous.state if previous is not None else None, "runtime-hook")
