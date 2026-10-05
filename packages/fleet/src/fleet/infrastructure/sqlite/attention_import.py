"""One-time preservation of legacy workspace attention actions."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .store import Store


def import_workspace(store: Store, path: Path) -> None:
    if not path.exists():
        return
    with store.unit_of_work() as work:
        key = str(path.resolve())
        if work.connection.execute("SELECT 1 FROM attention_import WHERE path = ?", (key,)).fetchone():
            return
        content = path.read_text()
        document = json.loads(content)
        actions = document.get("attention", {})
        for reference, action in actions.items():
            state = action["state"]
            if state not in ("acknowledged", "snoozed"):
                raise ValueError(f"unknown imported attention state: {state}")
            at = datetime.fromtimestamp(action["at"], timezone.utc).isoformat()
            until = datetime.fromtimestamp(action["until"], timezone.utc).isoformat() if state == "snoozed" else None
            existing = work.connection.execute(
                "SELECT state, at, until FROM attention_imported_action WHERE reference = ?", (reference,)).fetchone()
            if existing is not None:
                if tuple(existing) != (state, at, until):
                    raise ValueError(f"conflicting imported attention action: {reference}")
                continue
            work.connection.execute(
                "INSERT INTO attention_imported_action (reference, state, at, until) VALUES (?, ?, ?, ?)",
                (reference, state, at, until))
            work.record_change(f"attention-import:{reference}", "", state, "workspace-import")
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            with backup.open("x") as output:
                output.write(content)
        work.connection.execute("INSERT INTO attention_import (path) VALUES (?)", (key,))
        work.record_change(f"attention-import:{key}", "", "imported", "workspace-import")
