"""What the deck writes and derives on top of host state, shared by live and fixture decks.

A state class using LiveWorkspace provides `changed` (a Condition), `version`,
`workspace` (a WorkspaceStore), `board` (an AttentionBoard) and `known_projects()`.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Container

from fleet.attention import AttentionBoard
from fleet.workspace import WorkspaceStore


class LiveWorkspace:
    changed: threading.Condition
    version: int
    workspace: WorkspaceStore
    board: AttentionBoard

    def known_projects(self) -> Container[str]:
        raise NotImplementedError

    def bump(self) -> None:
        """Push a new document to every browser."""
        with self.changed:
            self.version += 1
            self.changed.notify_all()

    def set_focus(self, focus: str, projects: list[str], labels: list[str]) -> None:
        self.workspace.set_focus(focus, projects, labels, self.known_projects())
        self.bump()

    def document(self) -> dict[str, Any]:
        raise NotImplementedError

    def act_on_attention(self, action: str, item_id: str, seconds: float | None = None) -> None:
        self.document()   # brings the board up to date: an item may have resolved since the last push
        self.board.act(item_id, action, time.time(), seconds)
        self.bump()

    def with_attention(self, document: dict[str, Any]) -> dict[str, Any]:
        """Add the stored focus choices and attention items derived from the document's hosts."""
        return {**document, "focus": self.workspace.focus_snapshot(),
                "attention": self.board.items(document["hosts"], time.time())}

    def wait_for_change(self, seen_version: int, timeout: float) -> int:
        """Also wakes when a snooze ends, so the item comes back on every deck without a reload."""
        now = time.time()
        ending = self.board.snooze_ending(now)
        wait = timeout if ending is None else max(0.0, min(timeout, ending - now))
        with self.changed:
            self.changed.wait_for(lambda: self.version != seen_version, timeout=wait)
            if self.version == seen_version and ending is not None and time.time() >= ending:
                self.board.announce(ending)
                self.version += 1
            return self.version
