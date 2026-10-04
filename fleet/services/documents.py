"""Retain worker documents without blocking observation ingestion."""
from __future__ import annotations

import re
import threading
from typing import Any, Callable, Protocol

from fleet.transport import FleetError

STATUS_LINE = re.compile(r"^\s*\**FLEET_STATUS:.*$", re.MULTILINE)


def is_private(name: str) -> bool:
    """CLAUDE.local.md and other *.local.md files are someone's private local notes."""
    return name.lower().endswith(".local.md")


class DocumentStore(Protocol):
    def observe(self, project_id: str, host: str, job: dict[str, Any]) -> list[dict[str, Any]]: ...

    def keep(self, project_id: str, host: str, job_id: str, document: dict[str, Any], content: str,
             truncated: bool = False) -> None: ...

    def failed(self, project_id: str, host: str, job_id: str, document_id: str, error: str) -> None: ...


class DocumentKeeper:
    """Copies jobs' documents into the store in the background, so the stream never waits on a read.

    Only the newest listing of each job is kept; unchanged documents (same mtime and size) are skipped.
    A read that fails is recorded on the document and retried the next time the job is listed.
    """

    def __init__(self, store: DocumentStore, fetch: Callable[[str, str, str], dict[str, Any]],
                 keep_trace: Callable[[str, dict], None] | None = None) -> None:
        self.store, self.fetch = store, fetch
        self.keep_trace = keep_trace
        self.pending: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self.wake = threading.Condition()
        self.busy = False
        self.thread: threading.Thread | None = None

    def observe(self, host: str, project_id: str, job: dict[str, Any]) -> None:
        with self.wake:
            self.pending[(host, job["id"])] = (project_id, job)
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
            self.wake.notify_all()

    def run(self) -> None:
        while True:
            with self.wake:
                self.wake.wait_for(lambda: self.pending)
                (host, _), (project_id, job) = self.pending.popitem()
                self.busy = True
            try:
                self.copy(host, project_id, job)
            finally:
                with self.wake:
                    self.busy = False
                    self.wake.notify_all()

    def copy(self, host: str, project_id: str, job: dict[str, Any]) -> None:
        for document in self.store.observe(project_id, host, job):
            try:
                read = self.fetch(host, job["id"], document["id"])
                self.store.keep(project_id, host, job["id"], document, read["content"], bool(read.get("truncated")))
            except (FleetError, KeyError, OSError) as error:
                self.store.failed(project_id, host, job["id"], document["id"], str(error) or type(error).__name__)
        if self.keep_trace is not None and job.get("trace"):
            self.keep_trace(host, job)

    def settle(self, timeout: float) -> bool:
        """Wait until nothing is pending (tests and shutdown); true when settled."""
        with self.wake:
            return self.wake.wait_for(lambda: not self.pending and not self.busy, timeout=timeout)
