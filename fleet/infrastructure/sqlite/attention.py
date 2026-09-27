"""SQLite persistence for the Attention module."""

from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
import json
from typing import Iterator

from fleet.modules.attention import AttentionItem, ImportedAction, StreamContext

from .store import Store, UnitOfWork, connect


def decode(row) -> AttentionItem:
    values = dict(row)
    if values["stream_context"] is not None:
        values["stream_context"] = StreamContext(**json.loads(values["stream_context"]))
    for name in ("last_seen", "snooze_until", "acknowledged_at", "resolved_at"):
        if values[name] is not None:
            values[name] = datetime.fromisoformat(values[name])
    return AttentionItem(**values)


class AttentionRepository:
    def __init__(self, store: Store, work: UnitOfWork | None = None) -> None:
        self.store = store
        self.work = work

    @contextmanager
    def transaction(self) -> Iterator["AttentionRepository"]:
        with self.store.unit_of_work() as work:
            yield AttentionRepository(self.store, work)

    def rows(self, query: str, parameters: tuple = ()) -> list:
        if self.work is not None:
            return self.work.connection.execute(query, parameters).fetchall()
        with closing(connect(self.store.path)) as connection:
            return connection.execute(query, parameters).fetchall()

    def get(self, item_id: str) -> AttentionItem:
        rows = self.rows("SELECT * FROM attention_item WHERE id = ?", (item_id,))
        if not rows:
            raise LookupError(f"no attention item '{item_id}'")
        return decode(rows[0])

    def find(self, source: str, source_reference: str) -> AttentionItem | None:
        rows = self.rows("SELECT * FROM attention_item WHERE source = ? AND source_reference = ?",
                         (source, source_reference))
        return decode(rows[0]) if rows else None

    def list(self) -> list[AttentionItem]:
        return [decode(row) for row in self.rows("SELECT * FROM attention_item ORDER BY last_seen, id")]

    def imported_action(self, reference: str) -> ImportedAction | None:
        rows = self.rows("SELECT * FROM attention_imported_action WHERE reference = ?", (reference,))
        if not rows:
            return None
        row = rows[0]
        return ImportedAction(row["state"], datetime.fromisoformat(row["at"]),
                              datetime.fromisoformat(row["until"]) if row["until"] is not None else None)

    def save(self, item: AttentionItem, previous: str | None, actor: str) -> None:
        if self.work is None:
            raise RuntimeError("attention writes require a transaction")
        values = asdict(item)
        if values["stream_context"] is not None:
            values["stream_context"] = json.dumps(values["stream_context"])
        for name in ("last_seen", "snooze_until", "acknowledged_at", "resolved_at"):
            if values[name] is not None:
                values[name] = values[name].isoformat()
        columns = ", ".join(values)
        parameters = ", ".join("?" for _ in values)
        updates = ", ".join(f"{key} = excluded.{key}" for key in values if key != "id")
        self.work.connection.execute(
            f"INSERT INTO attention_item ({columns}) VALUES ({parameters}) ON CONFLICT(id) DO UPDATE SET {updates}",
            tuple(values.values()))
        self.work.record_change(f"attention:{item.id}", previous if previous is not None else "", item.state, actor)
