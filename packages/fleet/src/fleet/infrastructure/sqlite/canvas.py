"""SQLite adapter for canvas records, versions, the event log, operation results and layout."""
from __future__ import annotations

import json

from .repository import Repository


def encode(value: dict) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class CanvasRepository(Repository):
    """Every shared record change has a history entry; the event log, versions, operation results and personal
    layout are their own record and are written as observations."""

    def load(self, space: str) -> dict[str, dict[str, dict]]:
        records: dict[str, dict[str, dict]] = {}
        for row in self.rows("SELECT kind, id, record FROM canvas_record WHERE space = ?", (space,)):
            records.setdefault(row["kind"], {})[row["id"]] = json.loads(row["record"])
        return records

    def require_unit(self):
        if self.unit is None:
            raise RuntimeError("canvas writes require a transaction")
        return self.unit

    def save(self, space: str, kind: str, identity: str, record: dict | None, actor: str) -> None:
        if kind == 'charter':
            raise ValueError('charters belong to Fleet Records guidance, not canvas records')
        unit = self.require_unit()
        previous = self.rows("SELECT record FROM canvas_record WHERE space = ? AND kind = ? AND id = ?",
                             (space, kind, identity))
        before = previous[0]["record"] if previous else ""
        if record is None:
            unit.connection.execute("DELETE FROM canvas_record WHERE space = ? AND kind = ? AND id = ?",
                                    (space, kind, identity))
            after = ""
        else:
            after = encode(record)
            unit.connection.execute(
                "INSERT INTO canvas_record (space, kind, id, record) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(space, kind, id) DO UPDATE SET record = excluded.record", (space, kind, identity, after))
        if before != after:
            unit.record_change(f"canvas:{space}:{kind}:{identity}", before, after, actor)

    def present(self, space: str, kind: str, identity: str, record: dict) -> None:
        """Presentation (a region's colour or position, a note's text): no version, no history entry."""
        if kind == 'charter':
            raise ValueError('charters belong to Fleet Records guidance, not canvas records')
        unit = self.require_unit()
        unit.record_observation(
            "INSERT INTO canvas_record (space, kind, id, record) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(space, kind, id) DO UPDATE SET record = excluded.record",
            (space, kind, identity, encode(record)))

    def save_version(self, space: str, kind: str, identity: str, version: int, record: dict) -> None:
        if kind == 'charter':
            raise ValueError('charter versions belong to Fleet Records guidance history')
        self.require_unit().record_observation(
            "INSERT OR REPLACE INTO canvas_version (space, kind, id, version, record) VALUES (?, ?, ?, ?, ?)",
            (space, kind, identity, version, encode(record)))

    def versions(self, space: str, kind: str, identity: str) -> list[dict]:
        rows = self.rows("SELECT version, record FROM canvas_version WHERE space = ? AND kind = ? AND id = ? "
                         "ORDER BY version DESC", (space, kind, identity))
        return [json.loads(row["record"]) for row in rows]

    def append_event(self, space: str, event: dict) -> int:
        unit = self.require_unit()
        unit.record_observation("INSERT INTO canvas_event (space, record) VALUES (?, ?)", (space, encode(event)))
        return unit.connection.execute("SELECT last_insert_rowid()").fetchone()[0]

    def events(self, space: str, *, after: int = 0, limit: int | None = None) -> list[dict]:
        query = "SELECT seq, record FROM canvas_event WHERE space = ? AND seq > ? ORDER BY seq"
        parameters: tuple = (space, after)
        if limit is not None:
            query += " LIMIT ?"
            parameters += (limit,)
        return [json.loads(row["record"]) | {"seq": row["seq"]} for row in self.rows(query, parameters)]

    def last_event(self, space: str) -> int:
        rows = self.rows("SELECT COALESCE(MAX(seq), 0) AS seq FROM canvas_event WHERE space = ?", (space,))
        return rows[0]["seq"]

    def op_result(self, op_id: str) -> dict | None:
        rows = self.rows("SELECT result FROM canvas_op WHERE id = ?", (op_id,))
        return json.loads(rows[0]["result"]) if rows else None

    def remember_op(self, op_id: str, space: str, op: str, result: dict) -> None:
        self.require_unit().record_observation(
            "INSERT OR IGNORE INTO canvas_op (id, space, op, result) VALUES (?, ?, ?, ?)",
            (op_id, space, op, encode(result)))

    def layout(self, space: str, person: str) -> dict[str, dict]:
        rows = self.rows("SELECT object, record FROM canvas_layout WHERE space = ? AND person = ?", (space, person))
        return {row["object"]: json.loads(row["record"]) for row in rows}

    def set_layout(self, space: str, person: str, object_ref: str, props: dict | None) -> None:
        unit = self.require_unit()
        if props is None:
            unit.record_observation("DELETE FROM canvas_layout WHERE space = ? AND person = ? AND object = ?",
                                    (space, person, object_ref))
            return
        unit.record_observation(
            "INSERT INTO canvas_layout (space, person, object, record) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(space, person, object) DO UPDATE SET record = excluded.record",
            (space, person, object_ref, encode(props)))

    def spaces(self) -> list[str]:
        return [row["space"] for row in self.rows(
            "SELECT DISTINCT space FROM canvas_record WHERE kind = 'workflow' AND id = 'main' ORDER BY space")]
