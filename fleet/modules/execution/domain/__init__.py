"""Execution identity and recorded run facts."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class JobObservation:
    job: str
    status: str
    runtime: str | None
    start: datetime | None
    end: datetime | None
    observed_at: datetime | None

    def run_status(self) -> str:
        return {"running": "running", "done": "succeeded", "failed": "failed",
                "cancelled": "stopped", "lost": "failed", "queued": "unknown outcome", "stalled": "unknown outcome"}[self.status]


@dataclass(frozen=True)
class Action:
    id: str
    work_item: str | None
    source: str
    dispatch_reason: str | None = None
    actor: str | None = None
    idempotency_key: str | None = None
    payload_fingerprint: str | None = None
    project: str | None = None
    payload: dict | None = None


@dataclass(frozen=True)
class Claim:
    action: str
    run: str
    active: bool


@dataclass(frozen=True)
class Run:
    id: str
    action: str
    host: str
    remote_job_id: str
    runtime: str | None
    status: str
    reason: str | None
    start: datetime | None
    end: datetime | None
    last_observed: datetime | None

    def __post_init__(self) -> None:
        if not self.host.strip() or not self.remote_job_id.strip():
            raise ValueError("host and remote job ID are required")
        if self.status not in ("running", "succeeded", "failed", "stopped", "unknown outcome"):
            raise ValueError("unknown run status")


@dataclass(frozen=True)
class DispatchResult:
    run: Run
    created: bool
