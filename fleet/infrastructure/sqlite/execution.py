"""Atomic action/run links and their state history."""

import json
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Callable, Iterator

from fleet.modules.execution import Action, Claim, Run
from .store import Store, UnitOfWork, connect


def decode_run(payload: str) -> Run:
    values = json.loads(payload)
    for key in ("start", "end", "last_observed"):
        if values[key] is not None:
            values[key] = datetime.fromisoformat(values[key])
    return Run(**values)


class ExecutionRepository:
    def __init__(self, store: Store, unit: UnitOfWork | None = None,
                 collaborators: Callable | None = None) -> None:
        self.store, self.unit = store, unit
        self.collaborators = collaborators
        if unit is not None and collaborators is not None:
            self.work, self.workspace = collaborators(unit)

    @contextmanager
    def transaction(self) -> Iterator["ExecutionRepository"]:
        with self.store.unit_of_work() as unit:
            yield ExecutionRepository(self.store, unit, self.collaborators)

    def rows(self, query: str, parameters: tuple = ()) -> list:
        if self.unit is not None:
            return self.unit.connection.execute(query, parameters).fetchall()
        with closing(connect(self.store.path)) as connection:
            return connection.execute(query, parameters).fetchall()

    def find(self, host: str, job: str) -> Run | None:
        rows = self.rows("SELECT record FROM execution_run WHERE host = ? AND remote_job_id = ?", (host, job))
        return decode_run(rows[0]["record"]) if rows else None

    def actions(self) -> list[Action]:
        return [Action(**json.loads(row["record"])) for row in self.rows("SELECT record FROM execution_action ORDER BY rowid")]

    def runs(self) -> list[Run]:
        return [decode_run(row["record"]) for row in self.rows("SELECT record FROM execution_run ORDER BY rowid")]

    def save(self, action: Action, run: Run, actor: str) -> None:
        self.save_action(action, actor)
        self.save_run(run, actor)

    def save_action(self, action: Action, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        action_payload = json.dumps(asdict(action), sort_keys=True)
        self.unit.connection.execute("INSERT INTO execution_action (id, record, idempotency_key) VALUES (?, ?, ?)",
                                     (action.id, action_payload, action.idempotency_key))
        self.unit.record_change(f"execution:action:{action.id}", "", action_payload, actor)

    def save_run(self, run: Run, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        run_payload = json.dumps(asdict(run), default=lambda value: value.isoformat(), sort_keys=True)
        self.unit.connection.execute(
            "INSERT INTO execution_run (id, action, host, remote_job_id, record) VALUES (?, ?, ?, ?, ?)",
            (run.id, run.action, run.host, run.remote_job_id, run_payload))
        self.unit.record_change(f"execution:run:{run.id}", "", run_payload, actor)

    def claims(self) -> list[Claim]:
        return [Claim(row["action"], row["run"], bool(row["active"]))
                for row in self.rows("SELECT action, run, active FROM execution_claim ORDER BY rowid")]

    def save_claim(self, claim: Claim, actor: str) -> None:
        self.unit.connection.execute("INSERT INTO execution_claim VALUES (?, ?, ?)",
                                     (claim.action, claim.run, claim.active))
        self.unit.record_change(f"execution:claim:{claim.run}", "", "active", actor)

    def release_claim(self, run: str, actor: str) -> None:
        changed = self.unit.connection.execute("UPDATE execution_claim SET active = 0 WHERE run = ? AND active = 1", (run,))
        if changed.rowcount:
            self.unit.record_change(f"execution:claim:{run}", "active", "released", actor)

    def request(self, key: str, fingerprint: str) -> Run | None:
        rows = self.rows("SELECT fingerprint, run FROM execution_request WHERE key = ?", (key,))
        if not rows:
            return None
        if rows[0]["fingerprint"] != fingerprint:
            raise ValueError("idempotency key already used with a different payload")
        return decode_run(self.rows("SELECT record FROM execution_run WHERE id = ?", (rows[0]["run"],))[0]["record"])

    def save_request(self, key: str, fingerprint: str, run: str, actor: str) -> None:
        self.unit.connection.execute("INSERT INTO execution_request VALUES (?, ?, ?)", (key, fingerprint, run))
        self.unit.record_change(f"execution:request:{key}", "", run, actor)

    def update(self, run: Run, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        previous = self.rows("SELECT record FROM execution_run WHERE id = ?", (run.id,))[0]["record"]
        payload = json.dumps(asdict(run), default=lambda value: value.isoformat(), sort_keys=True)
        self.unit.connection.execute("UPDATE execution_run SET record = ? WHERE id = ?", (payload, run.id))
        self.unit.record_change(f"execution:run:{run.id}", previous, payload, actor)
