from fleet.container import configured_container
"""Re-key legacy Work, Attention, Execution and Library records; report unresolved names.

Run with FLEET_STORE and FLEET_CONFIG pointing to the controller being migrated:
uv run --frozen python scripts/migrate_project_ids.py
Ambiguous names stay unchanged. Merge the intended projects, then rerun.
"""


if __name__ == '__main__':
    for line in configured_container().migrate_project_ids():
        print(line)
