"""Read the audit trail for one subject: who changed which fields, when, and from which run."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Store(Protocol):
    def history_subjects(self) -> list[str]: ...

    def history(self, subjects: tuple[str, ...] = (), prefixes: tuple[str, ...] = (),
                since: datetime | None = None) -> list[dict[str, object]]: ...


# Fields every write refreshes; showing them would bury the change that mattered.
NOISE = {"updated", "next_step_recorded_at"}
# A work item's history includes the records that belong to it.
WORK_PARTS = ("work:criterion:", "work:relation:")


def parse_moment(text: str) -> datetime:
    """An ISO date or time; a date means midnight, and a time without a zone means UTC."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"not an ISO date or time: {text}") from error
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """An ISO date or time, or an age such as 30m, 12h or 7d."""
    match = re.fullmatch(r"(\d+)([mhd])", text.strip())
    if match is None:
        return parse_moment(text)
    unit = {"m": "minutes", "h": "hours", "d": "days"}[match.group(2)]
    return (now or datetime.now(timezone.utc)) - timedelta(**{unit: int(match.group(1))})


def parse(text: str) -> object:
    if text in ("", "null"):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def changes(before: object, after: object) -> list[dict]:
    if not isinstance(before, dict) and not isinstance(after, dict):
        return [] if before == after else [{"field": "state", "before": before, "after": after}]
    created = not isinstance(before, dict)
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    return [{"field": name, "before": before.get(name), "after": after.get(name)}
            for name in sorted(set(before) | set(after))
            if name not in NOISE and name != "id" and before.get(name) != after.get(name)
            and not (created and after.get(name) in (None, "", [], {}))]


def project_slice(workspace: object, project: str) -> dict | None:
    """One project's part of the workspace record: its entry, floor, focus and shuttering."""
    if not isinstance(workspace, dict):
        return None
    entry = (workspace.get("projects") or {}).get(project)
    if entry is None:
        return None
    return {**entry, "floor": (workspace.get("floors") or {}).get(project),
            "focus": ((workspace.get("focus") or {}).get("projects") or {}).get(project),
            "shuttered": (workspace.get("shuttered") or {}).get(project)}


def kind_of(subject: str) -> str:
    prefix = subject.rpartition(":")[0]
    return {"work:item": "work item", "work:criterion": "criterion", "work:relation": "relation",
            "workspace:management": "project", "execution:run": "run",
            "library:entry": "library entry", "records": "record"}.get(prefix, prefix.replace(":", " ") or subject)


def known_projects(store: Store) -> set[str]:
    latest = store.history(subjects=("workspace",))[:1]
    workspace = parse(latest[0]["to"]) if latest else None
    return set((workspace or {}).get("projects") or {}) if isinstance(workspace, dict) else set()


def resolve(store: Store, reference: str) -> tuple[str, str, str | None]:
    """(kind, id, subject) for a full subject, a full id or a unique id prefix; projects have no subject."""
    reference = reference.strip()
    if not reference:
        raise LookupError("give a subject: an id, an id prefix or a subject such as attention:<id>")
    subjects = store.history_subjects()
    if reference in subjects:
        return kind_of(reference), reference.rpartition(":")[2] or reference, reference
    qualifier, _, prefix = reference.rpartition(":")
    found: dict[tuple[str, str], str | None] = {}
    for subject in subjects:
        identity = subject.rpartition(":")[2]
        if identity.startswith(prefix) and subject.startswith(qualifier) and subject != identity:
            kind = kind_of(subject)
            found[(kind, identity)] = None if kind == "project" else subject
    for project in known_projects(store):
        if project.startswith(prefix) and qualifier in ("", "project"):
            found[("project", project)] = None
    if not found:
        raise LookupError(f"no history for '{reference}'")
    if len(found) > 1:
        names = ", ".join(f"{kind} {identity}" for kind, identity in sorted(found)[:5])
        more = f" and {len(found) - 5} more" if len(found) > 5 else ""
        raise LookupError(f"'{reference}' matches several subjects: {names}{more}; give more of the id")
    (kind, identity), subject = next(iter(found.items()))
    return kind, identity, subject


def entry(row: dict, kind: str, identity: str, change_list: list[dict]) -> dict:
    return {"sequence": row["sequence"], "time": row["time"], "actor": row["actor"], "subject": row["subject"],
            "kind": kind, "id": identity, "changes": change_list,
            "source_run": row["source_run"], "job": row["job"]}


def subject_history(store: Store, reference: str, since: datetime | None = None) -> dict:
    kind, identity, subject = resolve(store, reference)
    entries = []
    if kind == "project":
        for row in store.history(subjects=("workspace", "workspace:management:" + identity), since=since):
            if row["subject"] == "workspace":
                change_list = changes(project_slice(parse(row["from"]), identity),
                                      project_slice(parse(row["to"]), identity))
                if not change_list:
                    continue
            else:
                change_list = [{"field": "management repository", "before": parse(row["from"]),
                                "after": parse(row["to"])}]
            entries.append(entry(row, kind, identity, change_list))
    else:
        parts = WORK_PARTS if kind == "work item" else ("records:",) if kind == "decision" else ()
        for row in store.history(subjects=(subject,), prefixes=parts, since=since):
            before, after = parse(row["from"]), parse(row["to"])
            if row["subject"] != subject:
                record = after if isinstance(after, dict) else before if isinstance(before, dict) else {}
                if identity not in (record.get("work_item"), record.get("from_item"), record.get("to_item"),
                                    record.get("key") if kind == "decision" else None):
                    continue
            entries.append(entry(row, kind_of(row["subject"]), row["subject"].rpartition(":")[2],
                                 changes(before, after)))
    return {"kind": kind, "id": identity, "subject": subject, "entries": entries}
