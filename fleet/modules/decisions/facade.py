"""Commands and read-only accepted decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from fleet.modules.execution import ExecutionFacade

from .application import answer_question, propose, record_decision
from .application.ports import DecisionRepository
from .domain import Decision


class DecisionsFacade:
    def __init__(self, repository: DecisionRepository, clock: Callable[[], datetime],
                 execution: ExecutionFacade, *, authority=None, records=None) -> None:
        self.repository, self.clock = repository, clock
        self.execution = execution
        self.authority, self.records = authority, records

    def record(self, work_item: str, *, actor: str, activation: str, source_run: str,
               question: str, answer: str, context: str) -> Decision:
        authorization = self.authority().require('record_decision', work_item, actor=actor, activation=activation)
        return record_decision(self.repository, self.clock, self.records, authorization,
                               source_run, question, answer, context)

    def answer(self, identity: str, answer: str, *, actor: str,
               next_step: str | None = None) -> Decision:
        decision = answer_question(self.repository, self.clock, identity, answer, actor, next_step)
        self.execution.retry_deliveries(decision=decision.id)
        return decision

    def get(self, identity: str) -> Decision:
        return self.repository.get(identity)

    def list(self) -> list[Decision]:
        return self.repository.list()

    def propose(self, activation, *, question: str, change: str, reason: str):
        return propose(self.repository, self.clock, activation, question=question, change=change, reason=reason)

    def proposals(self):
        return self.repository.proposals()
