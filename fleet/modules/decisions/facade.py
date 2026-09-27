"""Commands and read-only accepted decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .application import answer_question
from .application.ports import DecisionRepository
from .domain import Decision


class DecisionsFacade:
    def __init__(self, repository: DecisionRepository, clock: Callable[[], datetime]) -> None:
        self.repository, self.clock = repository, clock

    def answer(self, identity: str, answer: str, *, actor: str,
               next_step: str | None = None) -> Decision:
        return answer_question(self.repository, self.clock, identity, answer, actor, next_step)

    def get(self, identity: str) -> Decision:
        return self.repository.get(identity)

    def list(self) -> list[Decision]:
        return self.repository.list()
