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


def store_path() -> Path:
    return Path(os.environ["FLEET_STORE"]) if "FLEET_STORE" in os.environ else transport.CONFIG_PATH.parent / "fleet.db"


def open_store(path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> Store:
    return Store(path if path is not None else store_path(), clock)


def open_attention(store: Store | None = None, *, workspace_path: Path | None = None) -> AttentionFacade:
    store = store if store is not None else open_store()
    import_workspace(store, workspace_path if workspace_path is not None else transport.CONFIG_PATH.parent / "workspace.json")
    return AttentionFacade(AttentionRepository(store), store.clock)
