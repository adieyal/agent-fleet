"""Commands and read-only accepted decisions."""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import uuid4
from datetime import datetime
from typing import Callable
from fleet.modules.execution import ExecutionFacade
from fleet.modules.attention import AttentionItem

from .application import answer_question, propose, record_attention, record_decision, record_guided, record_streamed
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

    def record_attention(self, item_id: str, *, actor: str, activation: str, source_run: str, command: str,
                         answer: str, principle: str, context: str, question: str | None = None,
                         effect: str | None = None, completed_item: AttentionItem | None = None,
                         retry_run: str | None = None) -> Decision:
        """Record an activation-bound triage command and its attention effect in one transaction."""
        return record_attention(self.repository, self.clock, self.records, self.authority(), item_id,
            actor=actor, activation=activation, source_run=source_run, command=command, answer=answer,
            principle=principle, context=context, question=question, effect=effect,
            completed_item=completed_item, retry_run=retry_run)

    def escalate_triage_guard(self, item_id: str, *, reason: str, mandate_version: str,
                              source_run: str | None = None) -> Decision:
        """Controller guard: commit the Decision, handover and archival intent together.

        The controller reconciles archival intents after committing its scheduling transaction.
        This is a system limit, independent of an agent's authority to escalate voluntarily.
        """
        with self.repository.transaction() as transaction:
            item = transaction.attention.get(item_id)
            decision = Decision(str(uuid4()), item.id, 'Why must the user handle this item?', reason,
                'triage:scheduler', json.dumps(dict(command='scheduler_guard', project=item.project,
                                                   subject=item.subject)),
                () if item.work_item is None else (item.work_item,), self.clock(),
                mandate_version=mandate_version, source_run=source_run,
                principle='Triage guard rails: bounded attempts and visible escalation')
            transaction.attention.escalate(item.id, actor=decision.actor, reason=reason)
            transaction.insert(decision)
            transaction.records.prepare(item.project, f'decisions/{decision.id}.json',
                json.dumps(asdict(decision), default=str), key=decision.id, actor=decision.actor,
                source_run=source_run)
        return decision

    def publish_triage_guards(self, project: str) -> None:
        """Resume guard archival from the durable Decision after its transaction commits."""
        for intent in self.records.intents():
            if (intent['project'] != project or intent['state'] == 'confirmed'
                    or not intent['path'].startswith('decisions/')):
                continue
            identity = intent['path'].removeprefix('decisions/').removesuffix('.json')
            try:
                decision = self.get(identity)
            except LookupError:
                continue
            if decision.actor == 'triage:scheduler':
                result = self.records.publish(intent, json.dumps(asdict(decision), default=str))
                if result['state'] != 'confirmed':
                    raise RuntimeError('triage Decision archive not confirmed: ' + str(result['error']))

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
