"""Raise and reconcile attention from reachable host observations."""

from typing import TYPE_CHECKING, TypedDict

from ..domain import StreamContext

if TYPE_CHECKING:
    from ..facade import AttentionFacade


class StepObservation(TypedDict):
    index: int
    status: str
    title: str
    started_at: float | None


class ActivityObservation(TypedDict):
    kind: str
    name: str
    summary: str
    ts: float | None


class WorkObservation(TypedDict):
    id: str
    project: str
    project_id: str | None


class JobObservation(WorkObservation):
    status: str
    steps: list[StepObservation]
    updated_at: float | None


class SessionObservation(WorkObservation):
    agent: str
    activity: ActivityObservation | None


class HostObservation(TypedDict):
    name: str
    ok: bool
    jobs: list[JobObservation]
    sessions: list[SessionObservation]


WAITING_TOOLS = {"AskUserQuestion": "is asking you a question", "ExitPlanMode": "has a plan for you to approve"}


def ingest_attention(attention: "AttentionFacade", host: HostObservation, *,
                     owners: set[str] | None = None, raise_items: bool = True) -> bool:
    if not host["ok"]:
        return False
    source = f"stream:{host['name']}"
    references = set()

    def record(work: WorkObservation, owner_type: str, occurrence: str, kind: str,
               reason: str, summary: str, since: float | None) -> None:
        owner_reference = f"{owner_type}:{host['name']}:{work['id']}"
        reference = f"{owner_reference}:{occurrence}"
        references.add(reference)
        if not raise_items or (owners is not None and owner_reference not in owners):
            return
        context = StreamContext(host["name"], owner_type, work["id"], work["project"],
                                work.get("project_id"), reason, summary, since)
        attention.raise_item(project=work["project_id"] if work.get("project_id") is not None else work["project"],
                             kind=kind, owner=owner_reference, source=source, source_reference=reference,
                             headline=" ".join(summary.split()[:12]), context_reference=owner_reference,
                             stream_context=context, actor="host-stream")

    for job in host["jobs"]:
        if job.get("status") not in ("failed", "blocked", "stalled"):
            continue
        wanted = "running" if job["status"] == "stalled" else job["status"]
        step = next((step for step in job.get("steps", []) if step.get("status") == wanted), None)
        since = step.get("started_at") if step else job.get("updated_at")
        occurrence = f"{step['index']}@{since}" if step else f"@{since}"
        summary = f"step {step['index'] + 1} {job['status']}: {step['title']}" if step else f"job {job['status']}"
        record(job, "job", f"{job['status']}:{occurrence}", "blocker", f"job status {job['status']}", summary, since)
    for session in host["sessions"]:
        activity = session.get("activity") or {}
        if activity.get("kind") != "tool" or activity.get("name") not in WAITING_TOOLS:
            continue
        summary = f"{session['agent']} {WAITING_TOOLS[activity['name']]}"
        if activity.get("summary"):
            summary += f": {activity['summary']}"
        record(session, "session", f"{activity['name']}@{activity.get('ts')}", "decision",
               f"session tool {activity['name']}", summary, activity.get("ts"))
    return attention.reconcile(source, references, owners=owners, actor="host-stream")
