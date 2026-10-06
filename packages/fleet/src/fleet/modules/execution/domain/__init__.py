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
class Usage:
    source: str
    reports: list[dict | None]

    @classmethod
    def from_worker(cls, job: dict) -> "Usage | None":
        if job.get("usage_schema_version") != 1 or job.get("usage") is None:
            return None
        return cls(**job["usage"])


@dataclass(frozen=True)
class JobObservation:
    job: str
    status: str
    runtime: str | None
    start: datetime | None
    end: datetime | None
    observed_at: datetime | None
    usage: Usage | None = None
    current_action: str | None = None
    action_observed_at: datetime | None = None
    step_work: list[dict] | None = None  # as Run.step_work; None when no step names its own work item

    def run_status(self) -> str:
        return {"running": "running", "done": "succeeded", "failed": "failed", "blocked": "failed",
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
    activation: str | None = None
    mandate_version: str | None = None
    # The constitution and charter versions attached to the job, pinned for retries and decisions.
    guidance: dict | None = None


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
    usage: Usage | None = None
    current_action: str | None = None
    action_observed_at: datetime | None = None
    # The job's steps that name their own work item, as last observed: {"index", "work_item", "status" (the
    # step's own: pending, running, done, failed, blocked, cancelled), "start", "end" (ISO times or None)}.
    # They attribute the run's activity to that work while the run stays linked to its action's item.
    step_work: list[dict] | None = None
    # What ran: a fleet job, interactive session, or serve-owned responder turn.
    # Responder remote_job_id is its reservation ID; protocol IDs live in timings.
    kind: str = "job"
    # The job's or session's host-local project label, its description or title, and its working directory.
    label: str | None = None
    title: str | None = None
    cwd: str | None = None
    # The job workspace as last read (see CONTEXT "Job workspace"), or None with the worker's reason.
    workspace: dict | None = None
    workspace_reason: str | None = None
    trace: dict | None = None
    # Local responder protocol IDs and timings, never a fleetd observation.
    timings: dict | None = None

    def __post_init__(self) -> None:
        if not self.host.strip() or not self.remote_job_id.strip():
            raise ValueError("host and remote job ID are required")
        if self.status not in ("running", "succeeded", "failed", "stopped", "unknown outcome"):
            raise ValueError("unknown run status")
        if self.kind not in ("job", "session", "responder"):
            raise ValueError("unknown run kind")


@dataclass(frozen=True)
class DispatchResult:
    run: Run
    created: bool
