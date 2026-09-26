"""The building (docs/design/workspace-prd.md: The building): one floor per live project, a fixed number of them.

Capacity is a setting in the Fleet config, `"capacity": 6` by default and at most 10
(ADR 0005). Raising it is a deliberate edit, never a side effect of starting work.

Which floor a project occupies is live state in workspace.json (see fleet.workspace):
a project gets the lowest free floor when it moves in and keeps it; focus and activity
never change it. A project registered outside the deck (`fleet project add`) moves
in the first time the deck sees it, if a floor is free. A project with no floor within
capacity is listed in the lobby, so its work never drops out of view.
"""
from __future__ import annotations

from typing import Any

from fleet import transport
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


def load_capacity() -> int:
    return capacity_of(transport.load_config())
