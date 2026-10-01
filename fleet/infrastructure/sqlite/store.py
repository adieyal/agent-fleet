"""Transactional controller storage and ordered state history."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .migrations import MIGRATIONS

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
            'INSERT INTO state_history (subject, "from", "to", actor, time, job) VALUES (?, ?, ?, ?, ?, ?)',
            (subject, from_state, to_state, actor, now.isoformat(), self.store.job),
        )
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
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None, job: str | None = None) -> None:
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.job = job
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

    def history_subjects(self) -> list[str]:
        with closing(connect(self.path)) as connection:
            return [row[0] for row in connection.execute("SELECT DISTINCT subject FROM state_history")]

    def history(self, subjects: tuple[str, ...] = (), prefixes: tuple[str, ...] = (),
                since: datetime | None = None) -> list[dict[str, object]]:
        """Entries for these subjects or subject prefixes, newest first, each with the run its job made."""
        matches = ["subject = ?"] * len(subjects) + ["subject LIKE ? ESCAPE '\\'"] * len(prefixes)
        if not matches:
            return []
        escaped = [prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for prefix in prefixes]
        query = f"""SELECT history.*, (SELECT MIN(run.id) FROM execution_run run
                                        WHERE run.remote_job_id = history.job) AS source_run
                    FROM state_history history WHERE ({' OR '.join(matches)})"""
        parameters: list[object] = [*subjects, *escaped]
        if since is not None:
            query += " AND time >= ?"
            parameters.append(since.isoformat())
        with closing(connect(self.path)) as connection:
            rows = connection.execute(query + " ORDER BY sequence DESC", parameters)
            return [dict(row) for row in rows]

    def history_span_before(self, before: datetime) -> tuple[int, str | None, str | None]:
        """How many entries are older than `before`, and the times of the oldest and newest of them."""
        with closing(connect(self.path)) as connection:
            row = connection.execute("SELECT COUNT(*), MIN(time), MAX(time) FROM state_history WHERE time < ?",
                                     (before.isoformat(),)).fetchone()
            return row[0], row[1], row[2]

    def prune_history(self, before: datetime, actor: str) -> int:
        """Delete entries older than `before`, leaving one entry that records the pruning itself."""
        with self.unit_of_work() as work:
            deleted = work.connection.execute("DELETE FROM state_history WHERE time < ?",
                                              (before.isoformat(),)).rowcount
            work.record_change("history", "", f"pruned {deleted} entries before {before.isoformat()}", actor)
        return deleted

    def latest_sequence(self) -> int:
        with closing(connect(self.path)) as connection:
            return connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM state_history").fetchone()[0]
