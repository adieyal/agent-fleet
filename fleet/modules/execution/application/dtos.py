from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InputResult:
    status: Literal["applied", "busy", "failed"]
    error: str | None = None


@dataclass(frozen=True)
class GrantRequest:
    host: str
    job: str
    step: int
    key: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class GrantResult:
    added: tuple[str, ...]
    continuation: int  # the index of the step that continues the refused one
