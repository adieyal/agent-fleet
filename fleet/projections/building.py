"""Building placement rendered from workspace-owned facts."""

from dataclasses import asdict

from fleet.modules.workspace import ProjectReference, Registry, WorkspaceFacade


def building_state(workspace: WorkspaceFacade, registry: Registry, capacity: int) -> dict:
    floors = {project_id: floor for project_id, floor in workspace.floors_snapshot().items()
              if project_id in registry.projects and floor <= capacity}
    shuttered = {identity: asdict(record) for identity, record in workspace.shuttered_snapshot().items()}
    focus = {project_id: workspace.focus_of(ProjectReference(project_id=project_id)) for project_id in floors}
    return {"capacity": capacity, "floors": floors, "focus": focus, "shuttered": shuttered,
            "no_floor": [project_id for project_id in registry.projects
                         if project_id not in floors and project_id not in shuttered]}
