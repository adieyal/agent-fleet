"""Decision persistence and transactional collaborators."""

from typing import ContextManager, Protocol

from fleet.modules.attention import AttentionFacade
from fleet.modules.work import WorkFacade
from fleet.modules.execution import ExecutionFacade
from ..domain import Decision


class DecisionRepository(Protocol):
    attention: AttentionFacade
    work: WorkFacade
    execution: ExecutionFacade

    def transaction(self) -> ContextManager["DecisionRepository"]: ...
    def insert(self, decision: Decision) -> None: ...
    def get(self, identity: str) -> Decision: ...
    def list(self) -> list[Decision]: ...
