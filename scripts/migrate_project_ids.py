"""Re-key legacy Work, Attention, Execution and Library records; report unresolved names.

Run with FLEET_STORE and FLEET_CONFIG pointing to the controller being migrated:
uv run --frozen python scripts/migrate_project_ids.py
Ambiguous names stay unchanged. Merge the intended projects, then rerun.
"""

from fleet.composition import open_store, open_workspace
from fleet.infrastructure.sqlite.migrations.project_ids import migrate_project_ids


if __name__ == '__main__':
    store = open_store()
    for line in migrate_project_ids(store, open_workspace(store)):
        print(line)
