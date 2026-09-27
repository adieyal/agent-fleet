"""SQLite adapter for Work-owned records."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from fleet.modules.attention import AttentionFacade
from fleet.modules.work import Criterion, EvidenceSpecification, Relation, Summary, WorkItem
from .store import Store, UnitOfWork
from .repository import Repository

RECORDS = {"item": WorkItem, "criterion": Criterion, "relation": Relation, "summary": Summary}


def encode(record: WorkItem | Criterion | Relation | Summary) -> str:
    return json.dumps(asdict(record), default=lambda value: value.isoformat(), sort_keys=True)


def decode(kind: str, payload: str) -> WorkItem | Criterion | Relation | Summary:
    values = json.loads(payload)
    for field in ("created", "updated", "met_at", "next_step_recorded_at"):
        if field in values and values[field] is not None:
            values[field] = datetime.fromisoformat(values[field])
    if kind == "criterion":
        if values["specification"] is not None:
            values["specification"] = EvidenceSpecification(**values["specification"])
        values["evidence"] = tuple(values["evidence"])
    return RECORDS[kind](**values)


class WorkRepository(Repository):
    def __init__(self, store: Store, attention: Callable[[UnitOfWork], AttentionFacade],
                 unit: UnitOfWork | None = None) -> None:
        super().__init__(store, unit)
        self.attention_factory = attention
        if unit is not None:
            self.attention = attention(unit)

    def bind(self, unit: UnitOfWork) -> None:
        self.attention = self.attention_factory(unit)

    def get(self, kind: str, identity: str) -> WorkItem | Criterion | Relation | Summary:
        if kind not in RECORDS:
            raise ValueError("unknown work record")
        rows = self.rows(f"SELECT record FROM work_{kind} WHERE id = ?", (identity,))
        if not rows:
            raise LookupError(f"no work {kind} '{identity}'")
        return decode(kind, rows[0]["record"])

    def list(self, kind: str) -> list:
        if kind not in RECORDS:
            raise ValueError("unknown work record")
        return [decode(kind, row["record"]) for row in self.rows(f"SELECT record FROM work_{kind} ORDER BY rowid")]

    def save(self, kind: str, record: WorkItem | Criterion | Relation | Summary, actor: str) -> None:
        if kind == 'summary':
            raise ValueError('summaries must be authored through Records')
        if self.unit is None:
            raise RuntimeError("work writes require a transaction")
        if kind not in RECORDS:
            raise ValueError("unknown work record")
        previous = self.rows(f"SELECT record FROM work_{kind} WHERE id = ?", (record.id,))
        payload = encode(record)
        self.unit.connection.execute(
            f"INSERT INTO work_{kind} (id, record) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET record = excluded.record",
            (record.id, payload))
        self.unit.record_change(f"work:{kind}:{record.id}", previous[0]["record"] if previous else "", payload, actor)

    def retire_summary(self, identity: str, actor: str) -> None:
        with self.transaction() as repository:
            unit = repository.unit
            unit.connection.execute('DELETE FROM work_summary WHERE id = ?', (identity,))
            unit.connection.execute('UPDATE state_history SET "from" = ?, "to" = ? WHERE subject = ?',
                                    ('repository cutover', 'repository cutover', 'work:summary:' + identity))
            unit.record_change('work:summary:' + identity, 'store-owned', 'repository-owned', actor)
