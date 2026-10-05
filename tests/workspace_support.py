from fleet.container import configured_container
"""Populate the workspace registry in existing adapter tests."""


def persist_registry(registry):
    def populate(current):
        current.projects = registry.projects
        current.owners = registry.owners
    configured_container().initialized_workspace().edit_registry(populate)
