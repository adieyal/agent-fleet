"""Workspace public contract."""

from .facade import WorkspaceFacade
from .application import WorkspaceState, Repository
from .domain.projects import Registry, Project, Link, Suggestion, normalize_repository, PROJECT_ID
from .domain.building import DEFAULT_CAPACITY, MAX_CAPACITY, NoVacancy, capacity_of
from .domain.choices import FOCUSES, AlreadyShuttered, NotShuttered, AlreadyHoused
