"""Controller configuration operations and explicit path validation."""
from __future__ import annotations

import os
from pathlib import Path

from fleet.transport import FleetError


def default_actor() -> str:
    """Who a CLI write is recorded as: the fleet job it runs inside, else the user at the terminal."""
    job = os.environ.get("FLEET_JOB_ID")
    return f"job:{job}" if job else "user"


def validate_paths(command: str) -> list[str]:
    messages = []
    for name in ("FLEET_CONFIG", "FLEET_STORE"):
        if name not in os.environ:
            continue
        path = Path(os.environ[name])
        if path.exists():
            continue
        if name == "FLEET_STORE" and command in ("web", "serve"):
            messages.append(f"Creating new store at {path} (FLEET_STORE)")
        elif name == "FLEET_STORE":
            raise FleetError(f"FLEET_STORE points to a missing file: {path}. Create the store there by running "
                             f"fleet serve once (it creates a missing store), or unset FLEET_STORE to use the "
                             f"default store")
        else:
            raise FleetError(f"FLEET_CONFIG points to a missing file: {path}. Unset FLEET_CONFIG to use the "
                             f"default config, or create the file with {{\"hosts\": {{}}}} in it")
    return messages


class Configuration:
    def __init__(self, transport, workspace):
        self.transport, self.workspace = transport, workspace

    def host_add(self, name: str, *, local: bool, ssh: str | None, python: str):
        self.workspace()
        config = self.transport.load_config()
        entry = {"ssh": None if local else (ssh or name), "python": python}
        previous = config.setdefault("hosts", {}).get(name)
        config["hosts"][name] = entry
        self.transport.save_config(config)
        return entry, previous

    def host_remove(self, name: str):
        workspace = self.workspace()
        config = self.transport.load_config()
        entry = config.get("hosts", {}).pop(name, None)
        if entry is None:
            raise FleetError(f"no host '{name}'; fleet hosts lists them")
        self.transport.save_config(config)
        links = sorted(f"{link.host}:{link.label}" for project in workspace.registry().projects.values()
                       for link in project.links if link.host == name)
        return entry, links

    def library_key(self, project: str, libraries: dict):
        """Keep a legacy registered-name key only when it unambiguously owns this library."""
        if project in libraries:
            return project
        workspace = self.workspace()
        name = workspace.registry().get(project).name
        if name in libraries and workspace.resolve_project(name) == project:
            return name
        return project

    def library_add(self, project: str, path: str, *, recursive: bool):
        project = self.workspace().resolve_project(project)
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise FleetError(f"not a directory: {root}")
        config = self.transport.load_config()
        libraries = config.setdefault("libraries", {})
        libraries[self.library_key(project, libraries)] = {"path": str(root), "recursive": True} if recursive else str(root)
        self.transport.save_config(config)
        return project, root

    def library_remove(self, project: str):
        project = self.workspace().resolve_project(project)
        config = self.transport.load_config()
        libraries = config.get("libraries", {})
        entry = libraries.pop(self.library_key(project, libraries), None)
        if entry is None:
            raise FleetError(f"no library '{project}'; fleet libraries lists them")
        self.transport.save_config(config)
        return project, entry if isinstance(entry, str) else entry["path"]

    def libraries(self):
        return self.transport.load_config().get('libraries', {})

    def web_settings(self):
        config = self.transport.load_config()
        return dict(libraries=config.get('libraries', {}), project_labels=config.get('project_labels', {}),
                    pipelines=config.get('pipelines', {}))
