"""Inspect controller ownership without creating or migrating a worker store."""
from contextlib import closing
from pathlib import Path
import sqlite3


def owns_job(path: Path, job: str, run: str | None = None) -> bool:
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            return connection.execute(
                'SELECT 1 FROM execution_run WHERE remote_job_id = ? OR id = ? LIMIT 1', (job, run)
            ).fetchone() is not None
    except sqlite3.DatabaseError:
        # An inaccessible store cannot establish controller ownership.
        return False
