"""Records metadata; narrative bodies never enter the store."""

import json

from .repository import Repository


class RecordsRepository(Repository):
    def list(self) -> list[dict]:
        return [json.loads(row['record']) for row in self.rows('SELECT record FROM records_intent ORDER BY rowid')]

    def by_key(self, project: str, key: str) -> dict | None:
        return next((r for r in self.list() if r['project'] == project and r['key'] == key), None)

    def current(self, project: str, path: str) -> dict | None:
        rows = self.rows('SELECT record FROM records_document WHERE project = ? AND path = ?', (project, path))
        return json.loads(rows[0]['record']) if rows else None

    def save(self, record: dict) -> None:
        with self.transaction() as repository:
            previous = repository.by_key(record['project'], record['key'])
            if previous == record:
                return
            payload = json.dumps(record, sort_keys=True)
            repository.unit.connection.execute('INSERT INTO records_intent VALUES (?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET record=excluded.record',
                (record['id'], record['project'], record['key'], payload))
            if record['state'] == 'confirmed':
                repository.unit.connection.execute('INSERT INTO records_document VALUES (?, ?, ?) '
                    'ON CONFLICT(project,path) DO UPDATE SET record=excluded.record',
                    (record['project'], record['path'], payload))
            repository.unit.record_change('records:' + record['id'], json.dumps(previous), payload, record['actor'])
