"""Ordered controller schema migrations."""

MIGRATIONS = (
    (
        """CREATE TABLE state_history (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            "from" TEXT NOT NULL,
            "to" TEXT NOT NULL,
            actor TEXT NOT NULL,
            time TEXT NOT NULL
        )""",
        "CREATE INDEX state_history_time ON state_history(time)",
    ),
    (
        """CREATE TABLE attention_item (
            id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            work_item TEXT,
            run TEXT,
            kind TEXT NOT NULL CHECK (kind IN ('decision', 'blocker', 'alert')),
            owner TEXT NOT NULL,
            source TEXT NOT NULL,
            source_reference TEXT NOT NULL,
            headline TEXT NOT NULL,
            context_reference TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('open', 'acknowledged', 'snoozed', 'resolved')),
            snooze_until TEXT,
            resolution_details TEXT,
            last_seen TEXT NOT NULL,
            UNIQUE (source, source_reference)
        )""",
        """CREATE TABLE attention_imported_action (
            reference TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            at TEXT NOT NULL,
            until TEXT
        )""",
        "CREATE TABLE attention_import (path TEXT PRIMARY KEY)",
    ),
    (
        "CREATE TABLE work_item (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
        "CREATE TABLE work_criterion (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
        "CREATE TABLE work_relation (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
        "CREATE TABLE work_summary (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    ),
)
