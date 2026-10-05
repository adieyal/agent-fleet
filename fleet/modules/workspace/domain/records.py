"""Typed workspace snapshots and command results."""

from __future__ import annotations

from dataclasses import dataclass, field

from .projects import Project
from .building import DEFAULT_CAPACITY


@dataclass
class Focus:
    projects: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Shuttered:
    at: float
    floor: int | None


@dataclass
class WorkspaceSnapshot:
    projects: list[Project] = field(default_factory=list)
    capacity: int = DEFAULT_CAPACITY
    focus: Focus = field(default_factory=Focus)
    floors: dict[str, int] = field(default_factory=dict)
    shuttered: dict[str, Shuttered] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectReference:
    project: str | None = None
    project_id: str | None = None


@dataclass(frozen=True)
class Placement:
    project_id: str
    floor: int | None


@dataclass(frozen=True)
class MergeResult:
    project_id: str
    merged: str
    freed: int | None
    floor: int | None
    counts: dict[str, int] = field(default_factory=dict)
