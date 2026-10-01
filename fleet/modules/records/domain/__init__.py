"""Structured mandate validation."""

import json
import re
from dataclasses import dataclass

from fleet.modules.authority import DECISION_AUTHORITY_COMMANDS


CONSTITUTION = 'constitution.md'


def charter_path(epic: str) -> str:
    return f'charters/{epic}.md'


@dataclass(frozen=True)
class Version:
    """One commit of a guidance document; number counts that document's commits from 1."""
    revision: str
    number: int
    actor: str
    time: str
    source_run: str | None


@dataclass(frozen=True)
class Guidance:
    """A constitution or charter body at a version. A charter inherits the constitution version in force when it
    was written (None when there was none), and the current one may be newer."""
    path: str
    body: str
    version: Version
    inherits: Version | None = None
    constitution: Version | None = None


class GuidanceConflict(ValueError):
    """The document changed after the editor opened it."""


IN_FORCE = re.compile(r'^##\s+Decisions in force\b.*$', re.IGNORECASE | re.MULTILINE)


def in_force(body: str, text: str) -> str:
    """body with text as the last item of its "Decisions in force" section, which is added at the end if missing;
    the item continues the section's numbering, or is a bullet."""
    heading = IN_FORCE.search(body)
    if heading is None:
        return f"{body.rstrip()}\n\n## Decisions in force\n\n- {text}\n"
    following = re.compile(r'^#{1,2}\s', re.MULTILINE).search(body, heading.end())
    end = len(body) if following is None else following.start()
    section = body[heading.end():end].rstrip()
    rest = body[end:]
    numbers = re.findall(r'^(\d+)\.\s', section, re.MULTILINE)
    line = f"{int(numbers[-1]) + 1}. {text}" if numbers else f"- {text}"
    # A blank line under the heading when the section was empty, and one before the next heading.
    added = f"{body[:heading.end()]}{section}\n{'' if section else chr(10)}{line}\n"
    return added + (f"\n{rest}" if rest else "")


GUIDANCE_FILES = {'constitution': 'CONSTITUTION.md', 'charter': 'CHARTER.md'}
GUIDANCE_ROLES = {'constitution': "the project's constitution", 'charter': "your epic's charter"}


def guidance_brief(guidance: dict, work_item: str) -> str:
    """The paragraph a guided job's first step opens with."""
    files = ' and '.join(f'{GUIDANCE_FILES[name]} ({GUIDANCE_ROLES[name]})' for name in GUIDANCE_FILES
                         if guidance[name] is not None)
    return (f"Guidance: your context directory has {files}. This brief overrides the "
            "charter, and the charter overrides the constitution. Decide yourself what they allow, and record each "
            f"decision with `fleet decision record --work-item {work_item} --question Q --answer A --principle P "
            "--actor A`, naming the principle you relied on. Escalate what they say to escalate.")


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
