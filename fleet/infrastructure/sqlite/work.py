"""SQLite adapter for Work-owned records."""

import json
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Callable, Iterator

from fleet.modules.attention import AttentionFacade
from fleet.modules.work import Criterion, EvidenceSpecification, Relation, Summary, WorkItem
from .store import Store, UnitOfWork, connect

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


class WorkRepository:
    def __init__(self, store: Store, attention: Callable[[UnitOfWork], AttentionFacade],
                 unit: UnitOfWork | None = None) -> None:
        self.store, self.attention_factory, self.unit = store, attention, unit
        if unit is not None:
            self.attention = attention(unit)

    @contextmanager
    def transaction(self) -> Iterator["WorkRepository"]:
        with self.store.unit_of_work() as unit:
            yield WorkRepository(self.store, self.attention_factory, unit)

    def rows(self, query: str, parameters: tuple = ()) -> list:
        if self.unit is not None:
            return self.unit.connection.execute(query, parameters).fetchall()
        with closing(connect(self.store.path)) as connection:
            return connection.execute(query, parameters).fetchall()

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
