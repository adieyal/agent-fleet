"""Immutable accepted answers."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Decision:
    id: str
    attention_item: str
    question: str
    answer: str
    actor: str
    context: str
    affected_work_items: tuple[str, ...]
    time: datetime

    def __post_init__(self) -> None:
        for name in ("answer", "actor"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")


def selected_answer(answer: str, options: tuple[str, ...]) -> str:
    if options and answer.strip().isdecimal():
        index = int(answer.strip()) - 1
        if not 0 <= index < len(options):
            raise ValueError("option number is out of range")
        return options[index]
    return answer
