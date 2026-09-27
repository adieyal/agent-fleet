"""Execution identity and recorded run facts."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Delivery:
    key: str
    decision: str
    run: str
    answer: str
    status: str = "pending"
    failures: int = 0
    error: str | None = None


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
                "cancelled": "stopped", "queued": "unknown outcome", "stalled": "unknown outcome"}[self.status]


@dataclass(frozen=True)
class Action:
    id: str
    work_item: str
    source: str


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
