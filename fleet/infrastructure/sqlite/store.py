"""Transactional controller storage and ordered state history."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .migrations import MIGRATIONS

RETENTION = timedelta(days=7)


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


class UnitOfWork:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.connection: sqlite3.Connection
        self.recorded = 0
        self.observed = 0

    def __enter__(self) -> UnitOfWork:
        self.connection = connect(self.store.path)
        self.connection.execute("BEGIN IMMEDIATE")
        return self

    def record_change(self, subject: str, from_state: str, to_state: str, actor: str) -> None:
        now = self.store.clock()
        self.connection.execute(
            'INSERT INTO state_history (subject, "from", "to", actor, time) VALUES (?, ?, ?, ?, ?)',
            (subject, from_state, to_state, actor, now.isoformat()),
        )
        self.connection.execute("DELETE FROM state_history WHERE time < ?", ((now - RETENTION).isoformat(),))
        self.recorded += 1

    def record_observation(self, statement: str, parameters: tuple) -> None:
        """Write a reading that is not a state change (such as a run's activity glyph), with no history entry.

        Only rows written here are exempt from the rule that every change has a history entry."""
        self.observed += self.connection.execute(statement, parameters).rowcount

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        try:
            if error_type is None:
                if self.connection.total_changes > self.observed and not self.recorded:
                    self.connection.rollback()
                    raise ValueError("a state change requires a history entry")
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


class Store:
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                for index, statements in enumerate(MIGRATIONS, start=1):
                    if index > version:
                        for statement in statements:
                            connection.execute(statement)
                        connection.execute(f"PRAGMA user_version = {index}")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self)

    def schema_version(self) -> int:
        with closing(connect(self.path)) as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    def history_after(self, sequence: int) -> list[dict[str, object]]:
        with closing(connect(self.path)) as connection:
            rows = connection.execute("SELECT * FROM state_history WHERE sequence > ? ORDER BY sequence", (sequence,))
            return [dict(row) for row in rows]

    def latest_sequence(self) -> int:
        with closing(connect(self.path)) as connection:
            return connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM state_history").fetchone()[0]
