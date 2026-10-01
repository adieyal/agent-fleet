"""Workspace aggregate storage and one-time legacy import."""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from fleet.modules.workspace import WorkspaceState, WorkspaceSnapshot
from fleet.infrastructure.config.workspace import decode_workspace, workspace_config
from .repository import Repository


class WorkspaceRepository(Repository):
    def read(self) -> WorkspaceSnapshot:
        return decode_workspace(json.loads(self.rows("SELECT record FROM workspace_state WHERE id = 1")[0][0]))

    def management_repository(self, project: str) -> str:
        rows = self.rows('SELECT path FROM workspace_management WHERE project = ?', (project,))
        if not rows:
            raise ValueError(f'management repository not registered for {project}; '
                             f'register a Git repository with: fleet project management {project} <path>')
        return rows[0]['path']

    def register_management_repository(self, project: str, path: str, actor: str) -> None:
        with super().transaction() as repository:
            unit = repository.unit
            row = unit.connection.execute('SELECT path FROM workspace_management WHERE project = ?', (project,)).fetchone()
            if row is not None:
                if row['path'] != path:
                    raise ValueError('management repository already registered')
                return
            unit.connection.execute('INSERT INTO workspace_management VALUES (?, ?)', (project, path))
            unit.record_change('workspace:management:' + project, '', path, actor)

    @contextmanager
    def transaction(self, actor: str):
        with super().transaction() as repository:
            unit = repository.unit
            before = unit.connection.execute("SELECT record FROM workspace_state WHERE id = 1").fetchone()[0]
            state = WorkspaceState(decode_workspace(json.loads(before)))
            yield state
            after = json.dumps(workspace_config(state.snapshot()), sort_keys=True)
            if json.loads(before) != json.loads(after):
                unit.connection.execute("UPDATE workspace_state SET record = ? WHERE id = 1", (after,))
                unit.record_change("workspace", before, after, actor)

    def initialize(self, config_path: Path, workspace_path: Path, initial: dict | None = None) -> None:
        with super().transaction() as repository:
            unit = repository.unit
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
            state = WorkspaceState(decode_workspace(record))
            encoded = json.dumps(workspace_config(state.snapshot()), sort_keys=True)
            unit.connection.execute("INSERT INTO workspace_state VALUES (1, ?)", (encoded,))
            unit.record_change("workspace", "null", encoded, "legacy-import")
