"""Durable project reservations; writes share the execution transaction."""
import json

from .repository import Repository


class TriageRepository(Repository):
    def get(self, project: str) -> dict:
        rows = self.rows('SELECT record FROM triage_scheduler WHERE project = ?', (project,))
        return json.loads(rows[0]['record']) if rows else {}

    def projects(self) -> list[str]:
        return [row['project'] for row in self.rows('SELECT project FROM triage_scheduler')]

    def save(self, project: str, state: dict) -> None:
        previous = self.get(project)
        if previous == state:
            return
        payload = json.dumps(state, sort_keys=True)
        self.unit.connection.execute(
            'INSERT INTO triage_scheduler VALUES (?, ?) ON CONFLICT(project) DO UPDATE SET record=excluded.record',
            (project, payload))
        self.unit.record_change('triage:' + project, json.dumps(previous, sort_keys=True), payload, 'triage:scheduler')
