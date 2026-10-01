"""SQLite persistence for the Attention module."""

from dataclasses import asdict
from datetime import datetime
import json

from fleet.modules.attention import (AttentionItem, ImportedAction, Question, QuestionOption, Refusal,
                                    StreamContext)

from .repository import Repository


def decode(row) -> AttentionItem:
    values = dict(row)
    values["options"] = tuple(json.loads(values["options"]))
    values["refusals"] = tuple(Refusal(**{**refusal, "rules": None if refusal["rules"] is None else tuple(refusal["rules"]),
                                          "denied_by": tuple(refusal.get("denied_by", ()))})
                               for refusal in json.loads(values["refusals"]))
    values["questions"] = tuple(Question(**{**question, "options": tuple(QuestionOption(**option)
                                                                          for option in question["options"])})
                                for question in json.loads(values["questions"]))
    if values["stream_context"] is not None:
        values["stream_context"] = StreamContext(**json.loads(values["stream_context"]))
    for name in TIMES:
        if values[name] is not None:
            values[name] = datetime.fromisoformat(values[name])
    return AttentionItem(**values)


TIMES = ("last_seen", "snooze_until", "acknowledged_at", "resolved_at", "owner_at")


class AttentionRepository(Repository):
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
        self.write(item)
        self.unit.record_change(f"attention:{item.id}", previous if previous is not None else "", item.state, actor)

    def save_owner(self, item: AttentionItem, previous: str, actor: str) -> None:
        """Save an item handed to a new owner; its history row says from whom, to whom and why."""
        self.write(item)
        self.unit.record_change(f"attention:{item.id}:owner", json.dumps({"owner": previous}),
                                json.dumps({"owner": item.owner, "reason": item.owner_reason}), actor)

    def write(self, item: AttentionItem) -> None:
        if self.unit is None:
            raise RuntimeError("attention writes require a transaction")
        values = asdict(item)
        values["options"] = json.dumps(values["options"])
        values["refusals"] = json.dumps(values["refusals"])
        values["questions"] = json.dumps(values["questions"])
        if values["stream_context"] is not None:
            values["stream_context"] = json.dumps(values["stream_context"])
        for name in TIMES:
            if values[name] is not None:
                values[name] = values[name].isoformat()
        columns = ", ".join(values)
        parameters = ", ".join("?" for _ in values)
        updates = ", ".join(f"{key} = excluded.{key}" for key in values if key != "id")
        self.unit.connection.execute(
            f"INSERT INTO attention_item ({columns}) VALUES ({parameters}) ON CONFLICT(id) DO UPDATE SET {updates}",
            tuple(values.values()))
