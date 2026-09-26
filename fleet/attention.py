"""Attention items (CONTEXT.md: Attention item), derived from what the host streams report.

Only genuine signals become items:

- a job whose status is `failed` or `stalled` (fleetd derives both from its steps and
  runner) is a **blocker**;
- a session whose latest activity is a call to a tool that stops and waits for the
  person at the terminal (`AskUserQuestion`, `ExitPlanMode`) is a **decision**. Once
  answered, the agent's next event replaces it as the latest activity.

A session's `idle` status is not a signal: fleetd infers it from 90 quiet seconds of
transcript, which a long tool call also produces. Headless jobs never wait on a person.

An item's id names its owner and the occurrence (the failing step's start, the tool
call's time), so it is stable across refreshes and a new failure after a retry is a new
item. Its state is `resolved` once the condition clears on a reachable host — the job
retried, removed or finished, the session moved on or went away. While the owner's host
is unreachable the item stays as it was, marked stale: unknown is not resolved.
Otherwise the state comes from the user's action in the workspace store: `acknowledged`,
`snoozed` until a time, or `open`. Reading an item never changes it.
"""
from __future__ import annotations

import threading
from typing import Any

from fleet.transport import FleetError
from fleet.workspace import WorkspaceStore

WAITING_TOOLS = {"AskUserQuestion": "is asking you a question", "ExitPlanMode": "has a plan for you to approve"}
RESOLVED_KEPT_SECONDS = 7 * 24 * 3600   # resolved items and stale actions are forgotten after a week


class ItemResolved(FleetError):
    """A resolved item has nothing left to acknowledge, snooze or reopen."""


def derive(host: dict[str, Any]) -> list[dict[str, Any]]:
    """Items for one host's jobs and sessions, as they are in a state document."""
    items = []
    for job in host.get("jobs", []):
        if job.get("status") not in ("failed", "stalled"):
            continue
        wanted = "failed" if job["status"] == "failed" else "running"
        step = next((step for step in job.get("steps", []) if step.get("status") == wanted), None)
        since = (step or {}).get("started_at") or job.get("updated_at")
        occurrence = f"{step['index']}@{since}" if step else f"@{since}"
        what = f"step {step['index'] + 1} {job['status']}: {step['title']}" if step else f"job {job['status']}"
        items.append(item(f"job:{host['name']}:{job['id']}:{job['status']}:{occurrence}", "blocker",
                          {"type": "job", "host": host["name"], "id": job["id"]}, f"job status {job['status']}",
                          what, job, since))
    for session in host.get("sessions", []):
        activity = session.get("activity") or {}
        if activity.get("kind") != "tool" or activity.get("name") not in WAITING_TOOLS:
            continue
        summary = f"{session.get('agent', 'agent')} {WAITING_TOOLS[activity['name']]}"
        if activity.get("summary"):
            summary += f": {activity['summary']}"
        items.append(item(f"session:{host['name']}:{session['id']}:{activity['name']}@{activity.get('ts')}", "decision",
                          {"type": "session", "host": host["name"], "id": session["id"]},
                          f"session tool {activity['name']}", summary, session, activity.get("ts")))
    return items


def item(item_id: str, kind: str, owner: dict[str, str], source: str, summary: str,
         work: dict[str, Any], since: float | None) -> dict[str, Any]:
    return {"id": item_id, "kind": kind, "owner": {**owner, "key": f"{owner['host']}:{owner['id']}"},
            "source": source, "summary": summary, "project": work.get("project"),
            "project_id": work.get("project_id"), "since": since}


class AttentionBoard:
    """Every item seen since the deck started, with the state it has now."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store
        self.known: dict[str, dict[str, Any]] = {}   # id → item, with resolved_at and stale
        self.lock = threading.Lock()
        self.woken_until = 0.0   # snoozes ending at or before this have been announced

    def items(self, hosts: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        with self.lock:
            reachable = {host["name"] for host in hosts if host.get("ok")}
            fresh = {derived["id"]: derived for host in hosts if host["name"] in reachable for derived in derive(host)}
            for item_id, derived in fresh.items():
                self.known[item_id] = {**derived, "resolved_at": None, "stale": False}
            resolved = []
            for item_id, known in list(self.known.items()):
                if item_id in fresh:
                    continue
                if known["resolved_at"] is not None:
                    if now - known["resolved_at"] > RESOLVED_KEPT_SECONDS:
                        del self.known[item_id]
                elif known["owner"]["host"] in reachable:
                    known["resolved_at"] = now
                    resolved.append(item_id)
                else:
                    known["stale"] = True
            actions = self.store.actions()
            stale_actions = [item_id for item_id, action in actions.items()
                             if item_id not in self.known and now - action.get("at", 0) > RESOLVED_KEPT_SECONDS]
            self.store.forget(resolved + stale_actions)
            return [self.present(known, actions.get(item_id), now)
                    for item_id, known in sorted(self.known.items(), key=lambda entry: entry[1]["since"] or 0)]

    @staticmethod
    def present(known: dict[str, Any], action: dict[str, Any] | None, now: float) -> dict[str, Any]:
        state, acknowledged_at, snoozed_until = "open", None, None
        if known["resolved_at"] is not None:
            state = "resolved"
        elif action and action["state"] == "acknowledged":
            state, acknowledged_at = "acknowledged", action["at"]
        elif action and action["state"] == "snoozed" and action["until"] > now:
            state, snoozed_until = "snoozed", action["until"]
        return {**known, "state": state, "acknowledged_at": acknowledged_at, "snoozed_until": snoozed_until}

    def act(self, item_id: str, action: str, now: float, seconds: float | None = None) -> None:
        """acknowledge, snooze (for `seconds`) or reopen a known, unresolved item."""
        with self.lock:
            known = self.known.get(item_id)
            if known is None:
                raise LookupError(f"no attention item '{item_id}'")
            if known["resolved_at"] is not None:
                raise ItemResolved(f"attention item '{item_id}' is resolved")
        if action == "acknowledge":
            self.store.act(item_id, {"state": "acknowledged", "at": now})
        elif action == "snooze":
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
                raise FleetError("snooze needs a positive number of seconds")
            self.store.act(item_id, {"state": "snoozed", "at": now, "until": now + seconds})
        elif action == "reopen":
            self.store.act(item_id, None)
        else:
            raise FleetError(f"unknown attention action '{action}'")

    def snooze_ending(self, now: float) -> float | None:
        """When the next snooze not yet announced ends, so the stream can wake browsers then."""
        ends = [action["until"] for action in self.store.actions().values()
                if action["state"] == "snoozed" and action["until"] > self.woken_until]
        return min(ends) if ends else None

    def announce(self, until: float) -> None:
        self.woken_until = max(self.woken_until, until)
