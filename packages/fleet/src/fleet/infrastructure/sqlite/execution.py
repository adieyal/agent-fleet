"""Atomic action/run links and their state history."""

import json
import hashlib
import os
import tempfile
from pathlib import Path
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from fleet.modules.execution import Action, Claim, Delivery, Run, Usage
from fleet.modules.decisions import Decision
from fleet.modules.attention import AttentionFacade
from .repository import Repository
from .store import Store, UnitOfWork


# A run's latest reading. It changes with every report from the host, so it is kept in execution_run_observation,
# which has no history; only the rest of the run is a state change. A finished run's usage stays in its record too.
OBSERVATION_FIELDS = ("last_observed", "current_action", "action_observed_at", "usage")
FINISHED = ("succeeded", "failed", "stopped")
RUN_ROWS = ("SELECT r.record, o.record AS observation FROM execution_run r "
            "LEFT JOIN execution_run_observation o ON o.run = r.id")


def encode_run(run: Run) -> tuple[dict, dict]:
    """The run as its record and its observation, both JSON-ready."""
    values = json.loads(json.dumps(asdict(run), default=lambda value: value.isoformat()))
    observation = {key: values.pop(key) for key in OBSERVATION_FIELDS}
    if run.status in FINISHED:
        values["usage"] = observation["usage"]
    return values, observation


def decode_run(row) -> Run:
    values = {**json.loads(row["record"]), **json.loads(row["observation"])}
    if values.get("usage") is not None:
        values["usage"] = Usage(**values["usage"])
    for key in ("start", "end", "last_observed"):
        if values[key] is not None:
            values[key] = datetime.fromisoformat(values[key])
    if values.get("action_observed_at") is not None:
        values["action_observed_at"] = datetime.fromisoformat(values["action_observed_at"])
    return Run(**values)


def dumps(values: dict) -> str:
    return json.dumps(values, sort_keys=True)


class ExecutionRepository(Repository):
    def __init__(self, store: Store, unit: UnitOfWork | None = None,
                 collaborators: Callable | None = None,
                 attention: Callable[[UnitOfWork], AttentionFacade] | None = None, decisions: Callable | None = None) -> None:
        super().__init__(store, unit)
        self.attention_factory = attention
        self.decisions_factory = decisions
        self.collaborators = collaborators
        if unit is not None:
            self.bind(unit)

    def bind(self, unit: UnitOfWork) -> None:
        if self.attention_factory is not None:
            self.attention = self.attention_factory(unit)
        if self.collaborators is not None:
            self.work, self.workspace = self.collaborators(unit)

    def record_answer_decision(self, decision) -> None:
        if self.unit is None or self.decisions_factory is None:
            raise RuntimeError("answer decision storage requires a transaction")
        self.decisions_factory(self.unit).insert(Decision(
            decision.id, decision.attention_item, decision.headline, decision.reply, decision.actor,
            decision.context_reference, decision.work_items, decision.recorded_at,
            source_run=decision.source_run))

    def deliveries(self) -> list[Delivery]:
        return [Delivery(**json.loads(row["record"])) for row in self.rows("SELECT record FROM execution_delivery ORDER BY rowid")]

    def save_delivery(self, delivery: Delivery, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("delivery writes require a transaction")
        rows = self.rows("SELECT record FROM execution_delivery WHERE id = ?", (delivery.key,))
        previous = rows[0]["record"] if rows else ""
        payload = json.dumps(asdict(delivery), sort_keys=True)
        self.unit.connection.execute("INSERT INTO execution_delivery (id, record) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET record = excluded.record", (delivery.key, payload))
        self.unit.record_change(f"execution:delivery:{delivery.key}", previous, payload, actor)

    def find(self, host: str, job: str) -> Run | None:
        rows = self.rows(RUN_ROWS + " WHERE r.host = ? AND r.remote_job_id = ?", (host, job))
        return decode_run(rows[0]) if rows else None

    def activation_run(self, activation: str, idempotency_key: str) -> Run:
        rows = self.rows(RUN_ROWS + ' JOIN execution_request q ON q.run = r.id '
            'JOIN execution_action a ON a.id = r.action '
            "WHERE q.key = ? AND json_extract(a.record, '$.activation') = ?",
            (idempotency_key, activation))
        if not rows:
            raise LookupError(f'no run for activation {activation} and request {idempotency_key}')
        return decode_run(rows[0])

    def actions(self) -> list[Action]:
        return [Action(**json.loads(row["record"])) for row in self.rows("SELECT record FROM execution_action ORDER BY rowid")]

    def activated_actions(self) -> list[Action]:
        return [Action(**json.loads(row["record"])) for row in self.rows(
            "SELECT record FROM execution_action INDEXED BY execution_activated_actions "
            "WHERE json_extract(record, '$.activation') IS NOT NULL ORDER BY rowid")]

    def get_action(self, identity: str) -> Action:
        rows = self.rows("SELECT record FROM execution_action WHERE id = ?", (identity,))
        if not rows:
            raise LookupError(f"no action '{identity}'")
        return Action(**json.loads(rows[0]["record"]))

    def get_run(self, identity: str) -> Run:
        rows = self.rows(RUN_ROWS + " WHERE r.id = ?", (identity,))
        if not rows:
            raise LookupError(f"no run '{identity}'")
        return decode_run(rows[0])

    def job_identities(self, host: str | None = None) -> list[tuple[str, str]]:
        query = "SELECT host, remote_job_id FROM execution_run WHERE COALESCE(json_extract(record, '$.kind'), 'job') = 'job'"
        parameters = () if host is None else (host,)
        if host is not None:
            query += " AND host = ?"
        return [(row["host"], row["remote_job_id"]) for row in self.rows(query, parameters)]

    def decision_runs(self) -> list[Run]:
        """Only worker runs that can still consume a new decision."""
        return [decode_run(row) for row in self.rows(RUN_ROWS +
            " WHERE json_extract(r.record, '$.status') IN ('running', 'unknown outcome')"
            " AND COALESCE(json_extract(r.record, '$.kind'), 'job') = 'job'")]

    def active_runs(self) -> list[Run]:
        return [decode_run(row) for row in self.rows(RUN_ROWS +
            " WHERE json_extract(r.record, '$.status') IN ('running', 'unknown outcome') ORDER BY r.rowid")]

    def runs(self) -> list[Run]:
        return [decode_run(row) for row in self.rows(RUN_ROWS + " ORDER BY r.rowid")]

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
        record, observation = encode_run(run)
        run_payload = dumps(record)
        self.unit.connection.execute(
            "INSERT INTO execution_run (id, action, host, remote_job_id, record) VALUES (?, ?, ?, ?, ?)",
            (run.id, run.action, run.host, run.remote_job_id, run_payload))
        self.unit.record_change(f"execution:run:{run.id}", "", run_payload, actor)
        self.unit.record_observation("INSERT INTO execution_run_observation (run, record) VALUES (?, ?)",
                                     (run.id, dumps(observation)))

    def update_action(self, action: Action, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        previous = self.get_action(action.id)
        if previous == action:
            return
        payload = dumps(asdict(action))
        self.unit.connection.execute("UPDATE execution_action SET record = ? WHERE id = ?", (payload, action.id))
        self.unit.record_change(f"execution:action:{action.id}", dumps(asdict(previous)), payload, actor)

    def claims(self) -> list[Claim]:
        return [Claim(row["action"], row["run"], bool(row["active"]))
                for row in self.rows("SELECT action, run, active FROM execution_claim ORDER BY rowid")]

    def steps(self, run: str) -> list[dict]:
        return [{"index": row["idx"], **json.loads(row["record"])}
                for row in self.rows("SELECT idx, record FROM execution_step WHERE run = ? ORDER BY idx", (run,))]

    def hosts(self) -> list[dict]:
        return [json.loads(row["record"]) for row in self.rows("SELECT record FROM execution_host ORDER BY name")]

    def trace_path(self, run: str, digest: str) -> Path:
        root = Path(os.environ.get("FLEET_HOME") or "~/.fleet").expanduser() / "traces"
        return root / hashlib.sha256(run.encode()).hexdigest() / f"{digest}.jsonl"

    def keep_trace(self, run: str, content: str) -> dict:
        payload = content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        path = self.trace_path(run, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return {"availability": "kept", "bytes": len(payload), "sha256": digest, "path": str(path)}

    def read_trace(self, run: str, record: dict) -> str | None:
        digest = record.get("sha256", "")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return None
        path = self.trace_path(run, digest)
        if not path.is_file():
            return None
        payload = path.read_bytes()
        return payload.decode("utf-8") if hashlib.sha256(payload).hexdigest() == digest else None

    def save_host(self, record: dict) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        rows = self.rows("SELECT record FROM execution_host WHERE name = ?", (record["name"],))
        previous = json.loads(rows[0]["record"]) if rows else None
        if previous == record:
            return
        statement = "INSERT INTO execution_host (name, record) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET record = excluded.record"
        parameters = (record["name"], dumps(record))
        if previous is None or previous["reachable"] != record["reachable"]:
            self.unit.connection.execute(statement, parameters)
            facts = lambda value: {key: item for key, item in value.items() if key != "last_observed"}
            self.unit.record_change(f"execution:host:{record['name']}", dumps(facts(previous)) if previous else "",
                                    dumps(facts(record)), "fleetd")
        else:
            self.unit.record_observation(statement, parameters)

    def save_step(self, run: str, index: int, record: dict, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        rows = self.rows("SELECT record FROM execution_step WHERE run = ? AND idx = ?", (run, index))
        previous = rows[0]["record"] if rows else ""
        if previous and json.loads(previous) == record:
            return
        payload = dumps(record)
        self.unit.connection.execute("INSERT INTO execution_step (run, idx, record) VALUES (?, ?, ?) "
                                     "ON CONFLICT(run, idx) DO UPDATE SET record = excluded.record",
                                     (run, index, payload))
        self.unit.record_change(f"execution:run:{run}:step:{index}", previous, payload, actor)

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
        return decode_run(self.rows(RUN_ROWS + " WHERE r.id = ?", (rows[0]["run"],))[0])

    def save_request(self, key: str, fingerprint: str, run: str, actor: str) -> None:
        self.unit.connection.execute("INSERT INTO execution_request VALUES (?, ?, ?)", (key, fingerprint, run))
        self.unit.record_change(f"execution:request:{key}", "", run, actor)

    def update(self, run: Run, actor: str) -> None:
        if self.unit is None:
            raise RuntimeError("execution writes require a transaction")
        previous = self.rows(RUN_ROWS + " WHERE r.id = ?", (run.id,))[0]
        record, observation = encode_run(run)
        # Compared as values: records the migration rewrote are spaced differently from json.dumps.
        if json.loads(previous["record"]) != record:
            payload = dumps(record)
            self.unit.connection.execute("UPDATE execution_run SET record = ? WHERE id = ?", (payload, run.id))
            self.unit.record_change(f"execution:run:{run.id}", previous["record"], payload, actor)
        if json.loads(previous["observation"]) != observation:
            self.unit.record_observation("UPDATE execution_run_observation SET record = ? WHERE run = ?",
                                         (dumps(observation), run.id))
