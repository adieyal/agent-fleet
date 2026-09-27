from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InputResult:
    status: Literal["applied", "busy", "failed"]
    error: str | None = None
