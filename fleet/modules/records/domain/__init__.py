"""Structured mandate validation."""

import json
from dataclasses import dataclass

from fleet.modules.authority import DECISION_AUTHORITY_COMMANDS


@dataclass(frozen=True)
class Mandate:
    goal: str
    constraints: list[str]
    decision_authority: list[str]
    escalation_conditions: list[str]
    criteria_it_may_judge: list[str]

    @classmethod
    def parse(cls, body: str) -> 'Mandate':
        try:
            value = cls(**json.loads(body))
            if not isinstance(value.goal, str) or not value.goal.strip():
                raise ValueError('goal required')
            for name in ('constraints', 'decision_authority', 'escalation_conditions', 'criteria_it_may_judge'):
                entries = getattr(value, name)
                if not isinstance(entries, list) or any(not isinstance(x, str) or not x.strip() for x in entries):
                    raise ValueError(name)
            unknown = [name for name in value.decision_authority if name not in DECISION_AUTHORITY_COMMANDS]
            if unknown:
                raise ValueError(f'unknown decision_authority entries: {", ".join(unknown)}; '
                                 f'valid names: {", ".join(DECISION_AUTHORITY_COMMANDS)}')
            return value
        except (TypeError, ValueError) as error:
            raise ValueError(f'invalid mandate: {error}') from error
