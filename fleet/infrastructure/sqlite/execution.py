"""Atomic action/run links and their state history."""

import json
from dataclasses import asdict
from datetime import datetime

from fleet.modules.execution import Action, Run
from .repository import Repository


def decode_run(payload: str) -> Run:
    values = json.loads(payload)
    for key in ("start", "end", "last_observed"):
        if values[key] is not None:
            values[key] = datetime.fromisoformat(values[key])
    return Run(**values)


class ExecutionRepository(Repository):
    def find(self, host: str, job: str) -> Run | None:
        rows = self.rows("SELECT record FROM execution_run WHERE host = ? AND remote_job_id = ?", (host, job))
        return decode_run(rows[0]["record"]) if rows else None

    def actions(self) -> list[Action]:
        return [Action(**json.loads(row["record"])) for row in self.rows("SELECT record FROM execution_action ORDER BY rowid")]

    def runs(self) -> list[Run]:
        return [decode_run(row["record"]) for row in self.rows("SELECT record FROM execution_run ORDER BY rowid")]

    def save(self, action: Action, run: Run, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        action_payload = json.dumps(asdict(action), sort_keys=True)
        run_payload = json.dumps(asdict(run), default=lambda value: value.isoformat(), sort_keys=True)
        self.unit.connection.execute("INSERT INTO execution_action (id, record) VALUES (?, ?)", (action.id, action_payload))
        self.unit.connection.execute(
            "INSERT INTO execution_run (id, action, host, remote_job_id, record) VALUES (?, ?, ?, ?, ?)",
            (run.id, run.action, run.host, run.remote_job_id, run_payload))
        self.unit.record_change(f"execution:action:{action.id}", "", action_payload, actor)
        self.unit.record_change(f"execution:run:{run.id}", "", run_payload, actor)

    def update(self, run: Run, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        previous = self.rows("SELECT record FROM execution_run WHERE id = ?", (run.id,))[0]["record"]
        payload = json.dumps(asdict(run), default=lambda value: value.isoformat(), sort_keys=True)
        self.unit.connection.execute("UPDATE execution_run SET record = ? WHERE id = ?", (payload, run.id))
        self.unit.record_change(f"execution:run:{run.id}", previous, payload, actor)
