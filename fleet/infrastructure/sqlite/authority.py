import json
from dataclasses import asdict

from fleet.modules.authority import Activation
from .repository import Repository


class AuthorityRepository(Repository):
    def get(self, identity: str) -> Activation:
        rows = self.rows('SELECT record FROM authority_activation WHERE id = ?', (identity,))
        if not rows:
            raise LookupError('unknown activation')
        return Activation(**json.loads(rows[0]['record']))

    def insert(self, activation: Activation) -> None:
        with self.transaction() as repository:
            payload = json.dumps(asdict(activation), sort_keys=True)
            repository.unit.connection.execute('INSERT INTO authority_activation VALUES (?, ?)', (activation.id, payload))
            repository.unit.record_change('activation:' + activation.id, '', payload, activation.actor)
