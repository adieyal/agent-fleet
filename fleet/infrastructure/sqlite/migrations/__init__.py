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
)
