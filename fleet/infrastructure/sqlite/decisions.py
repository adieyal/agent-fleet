"""Append-only decision storage with shared transactional collaborators."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from fleet.modules.attention import AttentionFacade
from fleet.modules.decisions import Decision, Proposal
from fleet.modules.work import WorkFacade
from fleet.modules.execution import ExecutionFacade
from fleet.modules.records import RecordsFacade
from .repository import Repository
from .store import Store, UnitOfWork


def decode(payload: str) -> Decision:
    values = json.loads(payload)
    values["time"] = datetime.fromisoformat(values["time"])
    values["affected_work_items"] = tuple(values["affected_work_items"])
    return Decision(**values)


class DecisionRepository(Repository):
    def __init__(self, store: Store, attention: Callable[[UnitOfWork], AttentionFacade],
                 work: Callable[[UnitOfWork], WorkFacade],
                 execution: Callable[[UnitOfWork], ExecutionFacade], *,
                 records: Callable[[UnitOfWork], RecordsFacade], unit: UnitOfWork | None = None) -> None:
        super().__init__(store, unit)
        self.attention_factory, self.work_factory = attention, work
        self.execution_factory = execution
        self.records_factory = records
        if unit is not None:
            self.bind(unit)

    def bind(self, unit: UnitOfWork) -> None:
        self.attention = self.attention_factory(unit)
        self.work = self.work_factory(unit)
        self.execution = self.execution_factory(unit)
        self.records = self.records_factory(unit)

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

    def insert_proposal(self, proposal: Proposal) -> None:
        payload = json.dumps(asdict(proposal), default=lambda value: value.isoformat(), sort_keys=True)
        self.unit.connection.execute('INSERT INTO decisions_proposal VALUES (?, ?)', (proposal.id, payload))
        self.unit.record_change('proposal:' + proposal.id, '', payload, proposal.actor)

    def proposals(self) -> list[Proposal]:
        result = []
        for row in self.rows('SELECT record FROM decisions_proposal ORDER BY rowid'):
            fields = json.loads(row['record'])
            fields['time'] = datetime.fromisoformat(fields['time'])
            result.append(Proposal(**fields))
        return result

    def get_proposal(self, identity: str) -> Proposal | None:
        rows = self.rows('SELECT record FROM decisions_proposal WHERE id = ?', (identity,))
        if not rows:
            return None
        fields = json.loads(rows[0]['record'])
        fields['time'] = datetime.fromisoformat(fields['time'])
        return Proposal(**fields)
