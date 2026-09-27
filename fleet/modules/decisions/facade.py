"""Commands and read-only accepted decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from fleet.modules.execution import ExecutionFacade

from .application import answer_question
from .application.ports import DecisionRepository
from .domain import Decision


class DecisionsFacade:
    def __init__(self, repository: DecisionRepository, clock: Callable[[], datetime],
                 execution: ExecutionFacade) -> None:
        self.repository, self.clock = repository, clock
        self.execution = execution

    def answer(self, identity: str, answer: str, *, actor: str,
               next_step: str | None = None) -> Decision:
        decision = answer_question(self.repository, self.clock, identity, answer, actor, next_step)
        self.execution.retry_deliveries(decision=decision.id)
        return decision

    def get(self, identity: str) -> Decision:
        return self.repository.get(identity)

    def list(self) -> list[Decision]:
        return self.repository.list()
