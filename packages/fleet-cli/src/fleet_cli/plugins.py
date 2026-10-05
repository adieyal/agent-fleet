"""Resolve optional commands through distribution metadata, without importing sibling packages."""
from importlib.metadata import entry_points

from fleet.container import FleetError


def load_command(name):
    matches = [entry for entry in entry_points(group='fleet.commands') if entry.name == name]
    if len(matches) != 1:
        raise FleetError(f"expected exactly one fleet.commands plugin named '{name}'; found {len(matches)}")
    command = matches[0].load()
    if not callable(command):
        raise FleetError(f"fleet.commands plugin '{name}' must be callable")
    return command
