"""Facade graph consumed by orchestration and triage services."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fleet.modules.attention import AttentionFacade
from fleet.modules.authority import AuthorityFacade
from fleet.modules.decisions import DecisionsFacade
from fleet.modules.execution import ExecutionFacade
from fleet.modules.library import LibraryFacade
from fleet.modules.records import RecordsFacade
from fleet.modules.work import WorkFacade
from fleet.modules.workspace import WorkspaceFacade


class Transaction(Protocol):
    """Opaque transaction token; consumers cannot access a database connection."""

    def record_change(self, subject: str, from_state: str, to_state: str, actor: str) -> None: ...


class StorePort(Protocol):
    path: Path

    def clock(self) -> datetime: ...
    def latest_sequence(self) -> int: ...
    def history_after(self, sequence: int) -> list[dict[str, object]]: ...


class TriageRecords(Protocol):
    def get(self, project: str) -> dict: ...
    def projects(self) -> list[str]: ...
    def save(self, project: str, state: dict) -> None: ...


@dataclass(frozen=True)
class TriageScope:
    records: TriageRecords
    services: Facades


class TriagePersistence(Protocol):
    def get(self, project: str) -> dict: ...
    def projects(self) -> list[str]: ...
    def transaction(self) -> AbstractContextManager[TriageScope]: ...


class Facades(Protocol):
    """Explicit capabilities; provider names are never resolved dynamically."""

    @property
    def store(self) -> StorePort: ...

    @property
    def unit(self) -> Transaction | None: ...

    @property
    def attention(self) -> AttentionFacade: ...

    @property
    def authority(self) -> AuthorityFacade: ...

    @property
    def decisions(self) -> DecisionsFacade: ...

    @property
    def execution(self) -> ExecutionFacade: ...

    @property
    def library(self) -> LibraryFacade: ...

    @property
    def records(self) -> RecordsFacade: ...

    @property
    def work(self) -> WorkFacade: ...

    @property
    def workspace(self) -> WorkspaceFacade: ...

    @property
    def triage_repository(self) -> TriagePersistence: ...

    def bound(self, unit: Transaction) -> Facades: ...
