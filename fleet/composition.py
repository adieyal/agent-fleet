"""Controller adapter construction."""

from __future__ import annotations

import os
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Callable

from fleet import transport
from fleet.infrastructure.sqlite import Store
from fleet.infrastructure.sqlite.attention import AttentionRepository
from fleet.infrastructure.sqlite.attention_import import import_workspace
from fleet.modules.attention import AttentionFacade
from fleet.modules.work import WorkFacade
from fleet.infrastructure.sqlite.work import WorkRepository
from fleet.infrastructure.documents.evidence import FileEvidenceReader
from fleet.infrastructure.sqlite.workspace import WorkspaceRepository
from fleet.modules.workspace import WorkspaceFacade
from fleet.infrastructure.sqlite.execution import ExecutionRepository
from fleet.infrastructure.sqlite.library import LibraryRepository
from fleet.modules.execution import ExecutionFacade
from fleet.modules.library import LibraryFacade
from fleet.modules.decisions import DecisionsFacade
from fleet.infrastructure.sqlite.decisions import DecisionRepository
from fleet.infrastructure.input_delivery import send_input
from fleet.infrastructure.sqlite.records import RecordsRepository
from fleet.infrastructure.git import RepositoryWriter
from fleet.modules.records import RecordsFacade
from fleet.modules.authority import AuthorityFacade
from fleet.infrastructure.sqlite.authority import AuthorityRepository


def store_path() -> Path:
    return Path(os.environ["FLEET_STORE"]) if "FLEET_STORE" in os.environ else transport.config_path().parent / "fleet.db"


def open_store(path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> Store:
    return Store(path if path is not None else store_path(), clock)


class Facades:
    def __init__(self, store, unit=None):
        self.store, self.unit = store, unit

    def bound(self, unit):
        return facades(self.store, unit)

    @cached_property
    def attention(self):
        return AttentionFacade(AttentionRepository(self.store, self.unit), self.store.clock)

    @cached_property
    def workspace_repository(self):
        return WorkspaceRepository(self.store, self.unit)

    @cached_property
    def workspace(self):
        return WorkspaceFacade(self.workspace_repository)

    @cached_property
    def records(self):
        return RecordsFacade(RecordsRepository(self.store), RepositoryWriter(), self.workspace, lambda: self.work)

    @cached_property
    def work(self):
        repository = WorkRepository(self.store, lambda unit: self.bound(unit).attention, self.unit)
        return WorkFacade(repository, FileEvidenceReader(), self.store.clock,
                          records=self.records, authority=lambda: self.authority)

    @cached_property
    def execution(self):
        repository = ExecutionRepository(self.store, self.unit,
            attention=lambda unit: self.bound(unit).attention,
            collaborators=lambda unit: (self.bound(unit).work, self.bound(unit).workspace))
        return ExecutionFacade(repository, self.work, send=send_input,
            prepare_dispatch=lambda: open_workspace(self.store), authority=lambda: self.authority)

    @cached_property
    def decisions(self):
        repository = DecisionRepository(self.store, lambda unit: self.bound(unit).attention,
            lambda unit: self.bound(unit).work, lambda unit: self.bound(unit).execution)
        return DecisionsFacade(repository, self.store.clock, self.execution,
                               records=self.records, authority=lambda: self.authority)

    @cached_property
    def authority(self):
        return AuthorityFacade(AuthorityRepository(self.store), self.records, self.work,
                               self.decisions, self.attention, self.execution)

    @cached_property
    def library(self):
        return LibraryFacade(LibraryRepository(self.store, self.unit), self.work)


def facades(store=None, unit=None):
    store = open_store() if store is None else store
    owner = store if unit is None else unit
    if not hasattr(owner, '_facades'):
        owner._facades = Facades(store, unit)
    return owner._facades


def open_attention(store: Store | None = None, *, workspace_path: Path | None = None) -> AttentionFacade:
    services = facades(store)
    import_workspace(services.store, workspace_path if workspace_path is not None else transport.config_path().parent / 'workspace.json')
    return services.attention


def open_work(store: Store | None = None) -> WorkFacade:
    return facades(store).work


def open_records(store: Store | None = None) -> RecordsFacade:
    return facades(store).records


def open_workspace(store: Store | None = None, *, initial: dict | None = None,
                   actor: str = 'user') -> WorkspaceFacade:
    services = facades(store)
    path = transport.config_path()
    services.workspace_repository.initialize(path, path.parent / 'workspace.json', initial)
    if actor == 'user':
        return services.workspace
    return WorkspaceFacade(services.workspace_repository, actor)


def open_execution(store: Store | None = None) -> ExecutionFacade:
    return facades(store).execution


def open_library(store: Store | None = None) -> LibraryFacade:
    return facades(store).library


def open_decisions(store: Store | None = None) -> DecisionsFacade:
    return facades(store).decisions


def open_authority(store: Store | None = None) -> AuthorityFacade:
    return facades(store).authority
