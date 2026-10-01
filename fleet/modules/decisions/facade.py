"""Commands and read-only accepted decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from fleet.modules.execution import ExecutionFacade

from .application import answer_question, propose, record_decision, record_guided, record_streamed
from .application.ports import DecisionRepository
from .domain import Decision, Proposal


class DecisionsFacade:
    def __init__(self, repository: DecisionRepository, clock: Callable[[], datetime],
                 execution: ExecutionFacade, *, authority=None, records=None) -> None:
        self.repository, self.clock = repository, clock
        self.execution = execution
        self.authority, self.records = authority, records

    def record(self, work_item: str, *, actor: str, activation: str, source_run: str,
               question: str, answer: str, context: str, principle: str | None = None) -> Decision:
        authorization = self.authority().require('record_decision', work_item, actor=actor, activation=activation)
        return record_decision(self.repository, self.clock, self.records, authorization,
                               source_run, question, answer, context, principle)

    def record_guided(self, work_item: str, *, actor: str, question: str, answer: str, principle: str,
                      context: str = "", source_run: str | None = None) -> Decision:
        return record_guided(self.repository, self.clock, work_item, actor=actor, question=question,
                             answer=answer, principle=principle, context=context, source_run=source_run)

    def record_streamed(self, identity: str, time: datetime, work_item: str, *, actor: str, question: str,
                        answer: str, principle: str, context: str, source_run: str | None) -> Decision:
        return record_streamed(self.repository, identity, time, work_item, actor=actor, question=question,
                               answer=answer, principle=principle, context=context, source_run=source_run)

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

    def proposal_for_attention(self, source: str, source_reference: str) -> Proposal | None:
        if source != 'proposal':
            return None
        return self.repository.get_proposal(source_reference)
