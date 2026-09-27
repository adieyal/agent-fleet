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


def store_path() -> Path:
    return Path(os.environ["FLEET_STORE"]) if "FLEET_STORE" in os.environ else transport.CONFIG_PATH.parent / "fleet.db"


def open_store(path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> Store:
    return Store(path if path is not None else store_path(), clock)


def open_attention(store: Store | None = None, *, workspace_path: Path | None = None) -> AttentionFacade:
    store = store if store is not None else open_store()
    import_workspace(store, workspace_path if workspace_path is not None else transport.CONFIG_PATH.parent / "workspace.json")
    return AttentionFacade(AttentionRepository(store), store.clock)


def open_work(store: Store | None = None) -> WorkFacade:
    store = store if store is not None else open_store()
    repository = WorkRepository(store, lambda unit: AttentionFacade(AttentionRepository(store, unit), store.clock))
    return WorkFacade(repository, FileEvidenceReader(), store.clock)


def open_workspace(store: Store | None = None, *, initial: dict | None = None,
                   actor: str = "user") -> WorkspaceFacade:
    repository = WorkspaceRepository(store if store is not None else open_store())
    repository.initialize(transport.CONFIG_PATH, transport.CONFIG_PATH.parent / "workspace.json", initial)
    return WorkspaceFacade(repository, actor)
