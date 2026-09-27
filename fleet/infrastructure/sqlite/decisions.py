"""Append-only decision storage with shared transactional collaborators."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from fleet.modules.attention import AttentionFacade
from fleet.modules.decisions import Decision
from fleet.modules.work import WorkFacade
from .repository import Repository
from .store import Store, UnitOfWork


def decode(payload: str) -> Decision:
    values = json.loads(payload)
    values["time"] = datetime.fromisoformat(values["time"])
    values["affected_work_items"] = tuple(values["affected_work_items"])
    return Decision(**values)


class DecisionRepository(Repository):
    def __init__(self, store: Store, attention: Callable[[UnitOfWork], AttentionFacade],
                 work: Callable[[UnitOfWork], WorkFacade]) -> None:
        super().__init__(store)
        self.attention_factory, self.work_factory = attention, work

    def bind(self, unit: UnitOfWork) -> None:
        self.attention = self.attention_factory(unit)
        self.work = self.work_factory(unit)

    def insert(self, decision: Decision) -> None:
        if self.unit is None:
            raise RuntimeError("decision writes require a transaction")
        payload = json.dumps(asdict(decision), default=lambda value: value.isoformat(), sort_keys=True)
        self.unit.connection.execute("INSERT INTO decisions_decision (id, record) VALUES (?, ?)",
                                     (decision.id, payload))
        self.unit.record_change(f"decision:{decision.id}", "", payload, decision.actor)

    def get(self, identity: str) -> Decision:
        rows = self.rows("SELECT record FROM decisions_decision WHERE id = ?", (identity,))
        if not rows:
            raise LookupError(f"no decision '{identity}'")
        return decode(rows[0]["record"])

    def list(self) -> list[Decision]:
        return [decode(row["record"]) for row in self.rows("SELECT record FROM decisions_decision ORDER BY rowid")]
