"""Workspace capacity limits and validation."""
from __future__ import annotations

from typing import Any

from fleet.transport import FleetError

DEFAULT_CAPACITY = 6
MAX_CAPACITY = 10


class NoVacancy(FleetError):
    """Every floor is taken."""


def capacity_of(config: dict[str, Any]) -> int:
    value = config.get("capacity", DEFAULT_CAPACITY)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CAPACITY:
        raise FleetError(f"capacity is a whole number of floors from 1 to {MAX_CAPACITY}, not {value!r}")
    return value
