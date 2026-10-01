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
    (
        "ALTER TABLE attention_item ADD COLUMN acknowledged_at TEXT",
        "ALTER TABLE attention_item ADD COLUMN resolved_at TEXT",
        "ALTER TABLE attention_item ADD COLUMN stream_context TEXT",
    ),
    (
        "CREATE TABLE execution_action (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
        """CREATE TABLE execution_run (
            id TEXT PRIMARY KEY, action TEXT NOT NULL REFERENCES execution_action(id),
            host TEXT NOT NULL, remote_job_id TEXT NOT NULL, record TEXT NOT NULL,
            UNIQUE (host, remote_job_id))""",
        "CREATE TABLE library_entry (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    ),
    (
        "CREATE TABLE workspace_state (id INTEGER PRIMARY KEY CHECK (id = 1), record TEXT NOT NULL)",
    ),
    (
        "UPDATE work_item SET record = json_set(record, '$.next_step_recorded_at', NULL)",
    ),
    (
        "ALTER TABLE attention_item ADD COLUMN options TEXT NOT NULL DEFAULT '[]'",
        "CREATE TABLE decisions_decision (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
        """CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions_decision
           BEGIN SELECT RAISE(ABORT, 'decisions are immutable'); END""",
        """CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions_decision
           BEGIN SELECT RAISE(ABORT, 'decisions are immutable'); END""",
    ),
    (
        'CREATE TABLE workspace_management (project TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE)',
        'CREATE TABLE records_intent (id TEXT PRIMARY KEY, project TEXT NOT NULL, key TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(project,key))',
        'CREATE TABLE records_document (project TEXT NOT NULL, path TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(project,path))',
    ),
    (
        "ALTER TABLE execution_action ADD COLUMN idempotency_key TEXT",
        "CREATE UNIQUE INDEX execution_action_key ON execution_action(idempotency_key)",
        """CREATE TABLE execution_claim (
            action TEXT NOT NULL, run TEXT PRIMARY KEY, active INTEGER NOT NULL CHECK(active IN (0, 1)))""",
        "CREATE UNIQUE INDEX execution_active_claim ON execution_claim(action) WHERE active = 1",
        """CREATE TABLE execution_request (
            key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, run TEXT NOT NULL)""",
    ),
    (
        "CREATE TABLE execution_delivery (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
    ),
    (
        'CREATE TABLE authority_activation (id TEXT PRIMARY KEY, record TEXT NOT NULL)',
        'CREATE TABLE decisions_proposal (id TEXT PRIMARY KEY, record TEXT NOT NULL)',
    ),
    (
        "ALTER TABLE attention_item ADD COLUMN refusals TEXT NOT NULL DEFAULT '[]'",
    ),
    (
        "ALTER TABLE attention_item ADD COLUMN questions TEXT NOT NULL DEFAULT '[]'",
    ),
    (
        # owner said both who must act ('user') and, for host-observed items, what the item is about
        # (job:<host>:<id>, session:…, run:…). The latter moves to subject; every existing item stays the user's.
        "ALTER TABLE attention_item ADD COLUMN subject TEXT",
        "ALTER TABLE attention_item ADD COLUMN owner_reason TEXT",
        "ALTER TABLE attention_item ADD COLUMN owner_actor TEXT",
        "ALTER TABLE attention_item ADD COLUMN owner_at TEXT",
        "UPDATE attention_item SET subject = owner WHERE owner != 'user'",
        "UPDATE attention_item SET owner = 'user'",
    ),
)
