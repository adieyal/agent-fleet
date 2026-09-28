"""Workspace capacity limits and validation."""
from __future__ import annotations

from fleet.errors import FleetError

DEFAULT_CAPACITY = 6
MAX_CAPACITY = 10


class NoVacancy(FleetError):
    """Every floor is taken."""


def capacity_of(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CAPACITY:
        raise FleetError(f"capacity is a whole number of floors from 1 to {MAX_CAPACITY}, not {value!r}")
    return value
