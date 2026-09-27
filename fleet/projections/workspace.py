"""JSON shapes for workspace storage and existing delivery contracts."""

from dataclasses import asdict

from fleet.modules.workspace import Project, ProjectReference, Registry, WorkspaceFacade, WorkspaceSnapshot
from .activity import with_activity


def project_config(project: Project) -> dict:
    entry = {"name": project.name,
             "links": [asdict(link) for link in sorted(project.links)],
             "repositories": list(project.repositories)}
    if project.created_at is not None:
        entry["created_at"] = project.created_at
    return entry


def registry_config(registry: Registry) -> dict:
    return {identity: project_config(project) for identity, project in sorted(registry.projects.items())}


def workspace_config(snapshot: WorkspaceSnapshot) -> dict:
    return {"projects": {project.id: project_config(project) for project in snapshot.projects},
            "capacity": snapshot.capacity, "focus": asdict(snapshot.focus),
            "floors": dict(snapshot.floors),
            "shuttered": {identity: asdict(record) for identity, record in snapshot.shuttered.items()}}


def resolve(registry: Registry, host: str, item: dict) -> dict:
    project = registry.project_for(host, item["project"]) if item.get("project") else None
    return {**item, "project_id": project.id if project else None}


def annotate(workspace: WorkspaceFacade, item: dict) -> dict:
    reference = ProjectReference(item.get("project"), item.get("project_id"))
    return {**with_activity(item), "focus": workspace.focus_of(reference)}
