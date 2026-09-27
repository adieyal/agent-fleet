"""Controller adapter construction."""

from __future__ import annotations

import os
from datetime import datetime
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


def store_path() -> Path:
    return Path(os.environ["FLEET_STORE"]) if "FLEET_STORE" in os.environ else transport.config_path().parent / "fleet.db"


def open_store(path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> Store:
    return Store(path if path is not None else store_path(), clock)


def open_attention(store: Store | None = None, *, workspace_path: Path | None = None) -> AttentionFacade:
    store = store if store is not None else open_store()
    import_workspace(store, workspace_path if workspace_path is not None else transport.config_path().parent / "workspace.json")
    return AttentionFacade(AttentionRepository(store), store.clock)


def open_work(store: Store | None = None) -> WorkFacade:
    store = store if store is not None else open_store()
    repository = WorkRepository(store, lambda unit: AttentionFacade(AttentionRepository(store, unit), store.clock))
    return WorkFacade(repository, FileEvidenceReader(), store.clock)


def open_workspace(store: Store | None = None, *, initial: dict | None = None,
                   actor: str = "user") -> WorkspaceFacade:
    repository = WorkspaceRepository(store if store is not None else open_store())
    path = transport.config_path()
    repository.initialize(path, path.parent / "workspace.json", initial)
    return WorkspaceFacade(repository, actor)


def open_execution(store: Store | None = None) -> ExecutionFacade:
    store = store if store is not None else open_store()

    def collaborators(unit):
        work = WorkFacade(WorkRepository(store, lambda bound: AttentionFacade(AttentionRepository(store, bound), store.clock),
                                        unit), FileEvidenceReader(), store.clock)
        return work, WorkspaceFacade(WorkspaceRepository(store, unit))

    return ExecutionFacade(ExecutionRepository(store, collaborators=collaborators), open_work(store),
                           prepare_dispatch=lambda: open_workspace(store))


def open_library(store: Store | None = None) -> LibraryFacade:
    store = store if store is not None else open_store()
    return LibraryFacade(LibraryRepository(store), open_work(store))


def open_decisions(store: Store | None = None) -> DecisionsFacade:
    store = store if store is not None else open_store()
    attention = lambda unit: AttentionFacade(AttentionRepository(store, unit), store.clock)
    work = lambda unit: WorkFacade(WorkRepository(store, attention, unit), FileEvidenceReader(), store.clock)
    return DecisionsFacade(DecisionRepository(store, attention, work), store.clock)
