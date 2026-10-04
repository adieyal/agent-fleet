"""Explicit provider overrides for web adapter fixtures."""
def override_container(container, *, documents=None, workspace=None):
    if documents is not None:
        container.project_documents.override(documents)
    if workspace is not None:
        container.workspace.override(workspace)
        container.initialized_workspace.override(workspace)
    return container
