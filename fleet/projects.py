"""Project registry: stable project identity, kept in the Fleet config (ADR 0001).

A job's `project` string is a *label* chosen on one host (fleetd derives it from a
repository directory name or `--project`). Labels are not identity: two hosts may
use the same label for unrelated work, and one project may carry different labels
on different hosts. The registry adds identity on top without changing labels:

- A registered project has a random, stable ID (`p-` + 8 hex chars) and a display
  name. The name can change freely and need not be unique.
- A project owns explicit links, each a (host, label) pair. A pair links to at most
  one project. Only a link attaches jobs to a project; matching names never do.
- A project may list repository remote URLs. They only *suggest* links for unlinked
  (host, label) pairs whose repository matches; accepting one is an explicit link.
- A (host, label) pair with no link stays an unregistered group, grouped and shown
  exactly as before the registry existed.

`project_labels` (label → friendly room name, host-agnostic) is kept as is and is
not migrated: turning it into links would merge every host's same-named label into
one project, which is the name matching ADR 0001 forbids. It remains a display name
for unregistered groups; a linked pair shows its project's name instead.

Stored under `projects` in the config file:

    "projects": {"p-1a2b3c4d": {"name": "Agent Fleet",
                                "links": [{"host": "home", "label": "agent-fleet"}],
                                "repositories": ["git@github.com:adieyal/agent-fleet.git"]}}
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable

from fleet import transport
from fleet.transport import FleetError

PROJECT_ID = re.compile(r"^p-[0-9a-f]{8}$")


@dataclass(frozen=True, order=True)
class Link:
    host: str
    label: str


@dataclass
class Project:
    id: str
    name: str
    links: list[Link] = field(default_factory=list)
    repositories: list[str] = field(default_factory=list)

    def to_config(self) -> dict[str, Any]:
        return {"name": self.name,
                "links": [{"host": link.host, "label": link.label} for link in sorted(self.links)],
                "repositories": list(self.repositories)}


@dataclass(frozen=True)
class Suggestion:
    """An unlinked (host, label) whose repository matches a registered project's."""
    link: Link
    project_id: str
    repository: str


def normalize_repository(url: str) -> str:
    """Compare remotes by host and path: ssh, https and scp-style forms of one repo are equal."""
    text = url.strip().lower().rstrip("/").removesuffix(".git")
    if "://" in text:
        authority, _, path = text.split("://", 1)[1].partition("/")
        host = authority.rsplit("@", 1)[-1].split(":")[0]
    elif ":" in text.split("/")[0]:
        authority, _, path = text.partition(":")
        host = authority.rsplit("@", 1)[-1]
    else:
        host, path = "", text
    return f"{host}/{path.strip('/')}"


class Registry:
    def __init__(self, projects: Iterable[Project] = ()) -> None:
        self.projects: dict[str, Project] = {}
        self.owners: dict[Link, str] = {}
        for project in projects:
            if not PROJECT_ID.match(project.id):
                raise FleetError(f"invalid project id '{project.id}' in config")
            self.projects[project.id] = Project(project.id, project.name)
            for repository in project.repositories:
                self.add_repository(project.id, repository)
            for link in project.links:
                self.link(project.id, link.host, link.label)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> Registry:
        return cls(Project(project_id, entry["name"],
                           [Link(link["host"], link["label"]) for link in entry.get("links", [])],
                           list(entry.get("repositories", [])))
                   for project_id, entry in config.get("projects", {}).items())

    def to_config(self) -> dict[str, Any]:
        return {project_id: project.to_config() for project_id, project in sorted(self.projects.items())}

    def get(self, project_id: str) -> Project:
        if project_id not in self.projects:
            raise FleetError(f"unknown project '{project_id}'")
        return self.projects[project_id]

    def create(self, name: str, repositories: Iterable[str] = ()) -> Project:
        if not name.strip():
            raise FleetError("a project needs a name")
        project_id = new_project_id()
        while project_id in self.projects:
            project_id = new_project_id()
        self.projects[project_id] = Project(project_id, name.strip())
        for repository in repositories:
            self.add_repository(project_id, repository)
        return self.projects[project_id]

    def rename(self, project_id: str, name: str) -> None:
        if not name.strip():
            raise FleetError("a project needs a name")
        self.get(project_id).name = name.strip()

    def remove(self, project_id: str) -> Project:
        """Forget a project; its labels fall back to unregistered groups."""
        project = self.get(project_id)
        for link in project.links:
            del self.owners[link]
        return self.projects.pop(project_id)

    def link(self, project_id: str, host: str, label: str) -> Link:
        project = self.get(project_id)
        link = Link(host, label)
        owner = self.owners.get(link)
        if owner == project_id:
            return link
        if owner is not None:
            raise FleetError(f"{host}:{label} is already linked to {owner} — unlink it first")
        project.links.append(link)
        self.owners[link] = project_id
        return link

    def unlink(self, host: str, label: str) -> str:
        link = Link(host, label)
        if link not in self.owners:
            raise FleetError(f"{host}:{label} is not linked to a project")
        project_id = self.owners.pop(link)
        self.projects[project_id].links.remove(link)
        return project_id

    def add_repository(self, project_id: str, url: str) -> None:
        project = self.get(project_id)
        if normalize_repository(url) not in map(normalize_repository, project.repositories):
            project.repositories.append(url.strip())

    def remove_repository(self, project_id: str, url: str) -> None:
        project = self.get(project_id)
        kept = [known for known in project.repositories if normalize_repository(known) != normalize_repository(url)]
        if len(kept) == len(project.repositories):
            raise FleetError(f"{project_id} has no repository {url}")
        project.repositories = kept

    def project_for(self, host: str, label: str) -> Project | None:
        """The project a job's (host, label) belongs to, or None for an unregistered group."""
        project_id = self.owners.get(Link(host, label))
        return self.projects[project_id] if project_id else None

    def display_name(self, host: str, label: str, project_labels: dict[str, str]) -> str | None:
        """Linked project name, else the `project_labels` entry, else None (show the label itself)."""
        project = self.project_for(host, label)
        return project.name if project else project_labels.get(label)

    def suggest_links(self, observed: Iterable[tuple[str, str, str]]) -> list[Suggestion]:
        """Suggest links for unlinked (host, label, repository remote) observations.

        Only a repository match suggests anything; an observation whose remote matches
        several projects is ambiguous and suggests them all for the user to choose.
        """
        by_repository: dict[str, list[str]] = {}
        for project_id, project in sorted(self.projects.items()):
            for repository in project.repositories:
                by_repository.setdefault(normalize_repository(repository), []).append(project_id)
        suggestions = []
        for host, label, remote in observed:
            link = Link(host, label)
            if link in self.owners or not remote:
                continue
            for project_id in by_repository.get(normalize_repository(remote), []):
                suggestions.append(Suggestion(link, project_id, remote))
        return suggestions


def new_project_id() -> str:
    return "p-" + secrets.token_hex(4)


def load_registry() -> Registry:
    return Registry.from_config(transport.load_config())


def save_registry(registry: Registry) -> None:
    """Write the registry back, keeping every other config key as it is on disk."""
    config = transport.load_config()
    config["projects"] = registry.to_config()
    transport.save_config(config)
