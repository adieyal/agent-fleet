"""Decision persistence and transactional collaborators."""

from __future__ import annotations

from typing import ContextManager, Protocol

from fleet.modules.attention import AttentionFacade
from fleet.modules.work import WorkFacade
from fleet.modules.execution import ExecutionFacade
from ..domain import Decision, Proposal


class DecisionRepository(Protocol):
    attention: AttentionFacade
    work: WorkFacade
    execution: ExecutionFacade

    def transaction(self) -> ContextManager["DecisionRepository"]: ...
    def insert(self, decision: Decision) -> None: ...
    def get(self, identity: str) -> Decision: ...
    def list(self) -> list[Decision]: ...
    def insert_proposal(self, proposal: Proposal) -> None: ...
    def proposals(self) -> list[Proposal]: ...
