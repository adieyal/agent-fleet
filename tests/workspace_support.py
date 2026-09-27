"""Populate the workspace registry in existing adapter tests."""

from fleet.composition import open_workspace


def persist_registry(registry):
    def populate(current):
        current.projects = registry.projects
        current.owners = registry.owners
    open_workspace().edit_registry(populate)
