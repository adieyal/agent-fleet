"""Library-owned index persistence."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import asdict

from fleet.modules.library import LibraryEntry
from .store import Store, connect


class LibraryRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, entry: LibraryEntry, actor: str) -> None:
        payload = json.dumps(asdict(entry), sort_keys=True)
        with self.store.unit_of_work() as unit:
            unit.connection.execute("INSERT INTO library_entry (id, record) VALUES (?, ?)", (entry.id, payload))
            unit.record_change(f"library:entry:{entry.id}", "", payload, actor)

    def list(self) -> list[LibraryEntry]:
        with closing(connect(self.store.path)) as connection:
            return [LibraryEntry(**json.loads(row["record"]))
                    for row in connection.execute("SELECT record FROM library_entry ORDER BY rowid")]
