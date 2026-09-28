"""Stable project identities and explicit host-label links."""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Iterable

from fleet.errors import FleetError

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
    created_at: float | None = None   # unknown for projects registered before it was recorded

@dataclass(frozen=True)
class Suggestion:
    """An unlinked (host, label) whose repository matches a registered project's."""
    link: Link
    project_id: str
    repository: str


# why a label may belong to a project, strongest first
REASONS = ("linked", "repository", "name")


@dataclass(frozen=True)
class Candidate:
    """A registered project a label may belong to, and why."""
    project_id: str
    reasons: tuple[str, ...]


def name_key(text: str) -> str:
    """Names compare without case, spaces or punctuation: "Agent Fleet" matches "agent-fleet"."""
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


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
            self.projects[project.id] = Project(project.id, project.name, created_at=project.created_at)
            for repository in project.repositories:
                self.add_repository(project.id, repository)
            for link in project.links:
                self.link(project.id, link.host, link.label)

    def get(self, project_id: str) -> Project:
        if project_id not in self.projects:
            raise FleetError(f"unknown project '{project_id}'")
        return self.projects[project_id]

    def resolve(self, reference: str) -> str:
        if reference in self.projects:
            return reference
        candidates = sorted(project.id for project in self.projects.values() if project.name == reference)
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            raise FleetError(f"ambiguous project '{reference}': {', '.join(candidates)}; "
                             "use an ID or fleet project merge <keep> <other>")
        raise FleetError(f"unknown project '{reference}'; use fleet project list to find a project ID")

    def create(self, name: str, repositories: Iterable[str] = ()) -> Project:
        if not name.strip():
            raise FleetError("a project needs a name")
        project_id = new_project_id()
        while project_id in self.projects:
            project_id = new_project_id()
        self.projects[project_id] = Project(project_id, name.strip(), created_at=time.time())
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

    def merge(self, keep_id: str, other_id: str) -> Project:
        """Fold `other` into `keep`, which must be the older when both ages are known (when either isn't, the caller
        chose): `keep` keeps its ID and name and gains the other's links and repositories; the other is forgotten."""
        if keep_id == other_id:
            raise FleetError("a project can't be merged with itself")
        keep, other = self.get(keep_id), self.get(other_id)
        if keep.created_at is not None and other.created_at is not None and other.created_at < keep.created_at:
            raise FleetError(f"{other_id} is older, so it is the one to keep: merge {other_id} {keep_id}")
        self.remove(other_id)
        for repository in other.repositories:
            self.add_repository(keep_id, repository)
        for link in other.links:
            self.link(keep_id, link.host, link.label)
        return keep

    def link_candidates(self, label: str, hosts: Iterable[str], remotes: Iterable[tuple[str, str, str]] = (),
                        display_name: str | None = None) -> list[Candidate]:
        """Projects the label on `hosts` may belong to: the label linked on another host, a repository matching one
        of `remotes` (host, label, url), or a name matching the label or its display name. Strongest first."""
        hosts = set(hosts)
        reasons: dict[str, set[str]] = {}
        for link, project_id in self.owners.items():
            if link.label == label and link.host not in hosts:
                reasons.setdefault(project_id, set()).add("linked")
        for suggestion in self.suggest_links((host, found, url) for host, found, url in remotes
                                             if found == label and host in hosts):
            reasons.setdefault(suggestion.project_id, set()).add("repository")
        keys = {name_key(text) for text in (label, display_name or "")} - {""}
        for project_id, project in self.projects.items():
            if name_key(project.name) in keys:
                reasons.setdefault(project_id, set()).add("name")
        found = [Candidate(project_id, tuple(reason for reason in REASONS if reason in why))
                 for project_id, why in reasons.items()]
        return sorted(found, key=lambda c: (REASONS.index(c.reasons[0]), -len(c.reasons),
                                            self.projects[c.project_id].name.lower(), c.project_id))

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
