"""Facade graph consumed by orchestration and triage services."""
from __future__ import annotations

from typing import Any, Protocol

from fleet.modules.attention import AttentionFacade
from fleet.modules.authority import AuthorityFacade
from fleet.modules.decisions import DecisionsFacade
from fleet.modules.execution import ExecutionFacade
from fleet.modules.library import LibraryFacade
from fleet.modules.records import RecordsFacade
from fleet.modules.work import WorkFacade
from fleet.modules.workspace import WorkspaceFacade


class Facades(Protocol):
    store: Any
    unit: Any
    attention: AttentionFacade
    authority: AuthorityFacade
    decisions: DecisionsFacade
    execution: ExecutionFacade
    library: LibraryFacade
    records: RecordsFacade
    work: WorkFacade
    workspace: WorkspaceFacade
    triage_repository: Any

    def bound(self, unit: Any) -> Facades: ...
