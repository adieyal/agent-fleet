"""Explicit, repeatable migration of legacy project names to Workspace IDs."""

import json

from fleet.errors import FleetError
from fleet.modules.workspace import WorkspaceFacade
from ..store import Store


def migrate_project_ids(store: Store, workspace: WorkspaceFacade) -> list[str]:
    report = []
    registry = workspace.registry()
    with store.unit_of_work() as unit:
        for table in ('work_item', 'execution_action', 'library_entry', 'attention_item'):
            for row in unit.connection.execute(f'SELECT * FROM {table}').fetchall():
                before = dict(row) if table == 'attention_item' else json.loads(row['record'])
                project = before['project']
                if project is None or project in registry.projects:
                    continue
                try:
                    identity = registry.resolve(project)
                except FleetError as error:
                    report.append(f'{table}:{row["id"]} unchanged: {error}')
                    continue
                after = {**before, 'project': identity}
                if table == 'attention_item':
                    unit.connection.execute('UPDATE attention_item SET project = ? WHERE id = ?', (identity, row['id']))
                else:
                    unit.connection.execute(f'UPDATE {table} SET record = ? WHERE id = ?',
                                            (json.dumps(after), row['id']))
                unit.record_change(f'{table}:{row["id"]}', json.dumps(before), json.dumps(after), 'project-id-migration')
                report.append(f'{table}:{row["id"]}: {project} -> {identity}')
    return report
