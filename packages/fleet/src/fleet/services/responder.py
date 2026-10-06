"""Responder runtime contract and serve-owned lifecycle.

The adapter owns protocol and process mechanics. Serve supervises this worker
with the same backoff and recovery reporting as its history and host workers.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from fleet.errors import FleetError


@dataclass(frozen=True)
class TurnResult:
    thread_id: str
    turn_id: str
    text: str
    status: str
    total_s: float
    first_delta_s: float | None
    start_s: float
    usage: dict[str, int] | None


class AppServerPort(Protocol):
    def start(self) -> None: ...
    def start_thread(self, *, timeout: float = 15) -> str: ...
    def turn(self, thread_id: str, prompt: str, *, output_schema: dict,
             effort: str = "low", timeout: float = 120,
             on_delta: Callable[[str], None] | None = None,
             on_complete: Callable[[TurnResult], None] | None = None) -> TurnResult: ...
    def interrupt(self, thread_id: str, turn_id: str, *, timeout: float = 5) -> None: ...
    def health(self) -> dict: ...
    def close(self) -> None: ...


class ResponderWorker:
    """Publish only initialized adapters; replace them after failure.

    A bootstrap thread warms thread initialization without spending model tokens.
    R2 will allocate and retain a separate thread per attention item.
    """

    def __init__(self, factory: Callable[[], AppServerPort]) -> None:
        self.factory = factory
        self.lock = threading.Lock()
        self.server: AppServerPort | None = None
        self.last_health: dict = {"alive": False, "ready": False, "pid": None,
                                  "codex_version": None, "last_turn": None}

    def run(self, stop: threading.Event, recovered: Callable[[str], None]) -> None:
        server = self.factory()
        with self.lock:
            self.server = server
        try:
            server.start()
            recovered("responder")
            while not stop.wait(0.1):
                health = server.health()
                if not health["alive"] or health.get("error"):
                    raise FleetError(health.get("error") or "responder app-server died")
        finally:
            server.close()
            with self.lock:
                final = server.health()
                final['last_turn'] = final.get('last_turn') or self.last_health.get('last_turn')
                self.last_health = final
                self.server = None

    def client(self) -> AppServerPort:
        with self.lock:
            if self.server is None or not self.server.health().get("ready"):
                health = self.server.health() if self.server else self.last_health
                raise FleetError("responder unavailable: " + (health.get('error') or 'app-server is not ready'))
            return self.server

    def health(self) -> dict:
        with self.lock:
            value = dict(self.server.health() if self.server else self.last_health)
            value['last_turn'] = value.get('last_turn') or self.last_health.get('last_turn')
            return value

    def close(self) -> None:
        # Cancel initialization and any outstanding turn before joining workers.
        with self.lock:
            server = self.server
        if server is not None:
            server.close()


def turn_health(result: TurnResult) -> dict:
    value = asdict(result)
    value.pop("text")
    return value
