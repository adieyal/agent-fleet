"""Decode persisted and legacy workspace JSON into module records."""

from fleet.modules.workspace import Focus, Link, Project, Shuttered, WorkspaceSnapshot, DEFAULT_CAPACITY


def decode_workspace(record: dict) -> WorkspaceSnapshot:
    projects = [
        Project(identity, entry["name"],
                [Link(**link) for link in entry.get("links", [])],
                list(entry.get("repositories", [])), entry.get("created_at"))
        for identity, entry in record.get("projects", {}).items()
    ]
    focus = record.get("focus") or {}
    return WorkspaceSnapshot(
        projects, record.get("capacity", DEFAULT_CAPACITY),
        Focus(dict(focus.get("projects") or {}), dict(focus.get("labels") or {})),
        dict(record.get("floors") or {}),
        {identity: Shuttered(entry["at"], entry.get("floor"))
         for identity, entry in (record.get("shuttered") or {}).items()},
    )
