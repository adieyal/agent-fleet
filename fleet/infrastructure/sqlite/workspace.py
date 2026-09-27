"""Workspace aggregate storage and one-time legacy import."""

from __future__ import annotations

import json
import shutil
from contextlib import closing, contextmanager
from pathlib import Path

from fleet.modules.workspace import WorkspaceState
from .store import Store, UnitOfWork, connect


class WorkspaceRepository:
    def __init__(self, store: Store, unit: UnitOfWork | None = None) -> None:
        self.store, self.unit = store, unit

    def read(self) -> dict:
        if self.unit is not None:
            return json.loads(self.unit.connection.execute("SELECT record FROM workspace_state WHERE id = 1").fetchone()[0])
        with closing(connect(self.store.path)) as connection:
            return json.loads(connection.execute("SELECT record FROM workspace_state WHERE id = 1").fetchone()[0])

    @contextmanager
    def transaction(self, actor: str):
        with self.store.unit_of_work() as unit:
            before = unit.connection.execute("SELECT record FROM workspace_state WHERE id = 1").fetchone()[0]
            state = WorkspaceState(json.loads(before))
            yield state
            after = json.dumps(state.snapshot(), sort_keys=True)
            if json.loads(before) != json.loads(after):
                unit.connection.execute("UPDATE workspace_state SET record = ? WHERE id = 1", (after,))
                unit.record_change("workspace", before, after, actor)

    def initialize(self, config_path: Path, workspace_path: Path, initial: dict | None = None) -> None:
        with self.store.unit_of_work() as unit:
            if unit.connection.execute("SELECT 1 FROM workspace_state WHERE id = 1").fetchone():
                return
            record = {} if initial is None else initial
            if initial is None:
                for path in (config_path, workspace_path):
                    if path.exists():
                        record.update(json.loads(path.read_text()))
                        backup = path.with_suffix(path.suffix + ".workspace.bak")
                        if not backup.exists():
                            shutil.copyfile(path, backup)
            state = WorkspaceState(record)
            encoded = json.dumps(state.snapshot(), sort_keys=True)
            unit.connection.execute("INSERT INTO workspace_state VALUES (1, ?)", (encoded,))
            unit.record_change("workspace", "null", encoded, "legacy-import")
