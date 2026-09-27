"""Library-owned index persistence."""

from __future__ import annotations

import json
from dataclasses import asdict

from fleet.modules.library import LibraryEntry
from .repository import Repository


class LibraryRepository(Repository):
    def save(self, entry: LibraryEntry, actor: str) -> None:
        payload = json.dumps(asdict(entry), sort_keys=True)
        with self.transaction() as repository:
            unit = repository.unit
            row = unit.connection.execute("SELECT record FROM library_entry WHERE id = ?", (entry.id,)).fetchone()
            previous = row["record"] if row is not None else ""
            if previous == payload:
                return
            unit.connection.execute("INSERT INTO library_entry (id, record) VALUES (?, ?) "
                                    "ON CONFLICT(id) DO UPDATE SET record = excluded.record", (entry.id, payload))
            unit.record_change(f"library:entry:{entry.id}", previous, payload, actor)

    def list(self) -> list[LibraryEntry]:
        return [LibraryEntry(**json.loads(row["record"]))
                for row in self.rows("SELECT record FROM library_entry ORDER BY rowid")]
