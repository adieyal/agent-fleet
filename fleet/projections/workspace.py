"""JSON shapes for workspace storage and existing delivery contracts."""

from dataclasses import asdict

from fleet.modules.workspace import ProjectReference, Registry, WorkspaceFacade
from .activity import with_activity


def registry_config(registry: Registry) -> dict:
    entries = {}
    for identity, project in sorted(registry.projects.items()):
        entry = dict(name=project.name, links=[asdict(link) for link in sorted(project.links)],
                     repositories=list(project.repositories))
        if project.created_at is not None:
            entry['created_at'] = project.created_at
        entries[identity] = entry
    return entries


def resolve(registry: Registry, host: str, item: dict) -> dict:
    project = registry.project_for(host, item["project"]) if item.get("project") else None
    return {**item, "project_id": project.id if project else None}


def annotate(workspace: WorkspaceFacade, item: dict) -> dict:
    reference = ProjectReference(item.get("project"), item.get("project_id"))
    return {**with_activity(item), "focus": workspace.focus_of(reference)}
