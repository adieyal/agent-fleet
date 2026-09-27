"""Seed the sourced Restoke V2 snapshot through Work.

Run: uv run --frozen python scripts/seed_supplier_slice.py
FLEET_STORE selects the destination. Source documents are never opened at runtime.
The adjacent JSON records source paths for every authored field. Titles and parent
paths identify snapshot items; reruns refresh their sourced fields.
"""

import json
from pathlib import Path

from fleet.composition import open_work
from fleet.modules.work import WorkFacade


def seed(work: WorkFacade) -> None:
    records = json.loads(Path(__file__).with_suffix(".json").read_text())
    existing = work.list(project="Restoke V2")
    identities: dict[str, str] = {}
    for record in records:
        parent = identities[record["parent"]] if record["parent"] is not None else None
        goal = record["goal"] + "\nSource: " + "; ".join(record["sources"])
        matches = [item for item in existing if item.parent == parent
                   and item.kind == record["kind"] and item.title == record["title"]]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous seed item: {record['title']}")
        if matches:
            item = matches[0]
        else:
            item = work.add(project="Restoke V2", parent=parent, kind=record["kind"],
                            title=record["title"], goal=goal, next_step=record["next_step"],
                            actor="supplier-slice-seed")
        changes = {name: value for name, value in {
            "goal": goal, "condition": record["condition"],
            "next_step": record["next_step"], "resume_condition": None,
        }.items() if getattr(item, name) != value}
        if changes:
            item = work.set(item.id, actor="supplier-slice-seed", **changes)
        identities[record["key"]] = item.id


if __name__ == "__main__":
    seed(open_work())
