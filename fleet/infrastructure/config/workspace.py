"""Decode persisted and legacy workspace JSON into module records."""

from fleet.modules.workspace import Focus, Link, Project, Shuttered, WorkspaceSnapshot, DEFAULT_CAPACITY
from dataclasses import asdict


def project_config(project: Project) -> dict:
    entry = {'name': project.name,
             'links': [asdict(link) for link in sorted(project.links)],
             'repositories': list(project.repositories)}
    if project.created_at is not None:
        entry['created_at'] = project.created_at
    return entry


def workspace_config(snapshot: WorkspaceSnapshot) -> dict:
    return {'projects': {project.id: project_config(project) for project in snapshot.projects},
            'capacity': snapshot.capacity, 'focus': asdict(snapshot.focus),
            'floors': dict(snapshot.floors),
            'shuttered': {identity: asdict(record) for identity, record in snapshot.shuttered.items()}}


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
