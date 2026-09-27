"""Controller adapter construction."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from fleet import transport
from fleet.infrastructure.sqlite import Store


def store_path() -> Path:
    return Path(os.environ["FLEET_STORE"]) if "FLEET_STORE" in os.environ else transport.CONFIG_PATH.parent / "fleet.db"


def open_store(path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> Store:
    return Store(path if path is not None else store_path(), clock)
