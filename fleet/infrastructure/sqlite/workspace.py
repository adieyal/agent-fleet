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
            state = WorkspaceState(decode_workspace(json.loads(before)),
                                   lambda keep, other: repository.rehome_records(keep, other, actor))
            yield state
            after = json.dumps(workspace_config(state.snapshot()), sort_keys=True)
            if json.loads(before) != json.loads(after):
                unit.connection.execute("UPDATE workspace_state SET record = ? WHERE id = 1", (after,))
                unit.record_change("workspace", before, after, actor)

    def rehome_records(self, keep: str, other: str, actor: str) -> dict[str, int]:
        """Move ownership in the identity transaction; linked runs/decisions stay immutable."""
        unit = self.unit
        connection = unit.connection
        work_ids = {row['id'] for row in connection.execute(
            "SELECT id FROM work_item WHERE json_extract(record, '$.project') = ?", (other,))}
        attention_ids = {row['id'] for row in connection.execute(
            'SELECT id FROM attention_item WHERE project = ?', (other,))}
        counts = dict(work_items=0, attention=0, runs=0, decisions=0)
        counts['runs'] = connection.execute("""SELECT count(*) FROM execution_run
            WHERE action IN (SELECT id FROM execution_action
                             WHERE json_extract(record, '$.project') = ?
                             OR json_extract(record, '$.work_item') IN
                                (SELECT id FROM work_item WHERE json_extract(record, '$.project') = ?))""",
            (other, other)).fetchone()[0]
        counts['decisions'] = sum(
            bool(work_ids.intersection(record['affected_work_items'])) or record.get('attention_item') in attention_ids
            for record in (json.loads(row['record'])
                           for row in connection.execute('SELECT record FROM decisions_decision')))
        for table in ('work_item', 'execution_action'):
            for row in connection.execute(
                    f"SELECT id, record FROM {table} WHERE json_extract(record, '$.project') = ?", (other,)).fetchall():
                before = row['record']
                after = json.dumps({**json.loads(before), 'project': keep}, sort_keys=True)
                connection.execute(f'UPDATE {table} SET record = ? WHERE id = ?', (after, row['id']))
                unit.record_change(f'{table}:{row["id"]}', before, after, actor)
                if table == 'work_item':
                    counts['work_items'] += 1
        for row in connection.execute('SELECT * FROM attention_item WHERE project = ?', (other,)).fetchall():
            before = dict(row)
            connection.execute('UPDATE attention_item SET project = ? WHERE id = ?', (keep, row['id']))
            unit.record_change('attention:' + row['id'], json.dumps(before),
                               json.dumps({**before, 'project': keep}), actor)
            counts['attention'] += 1
        return counts

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
