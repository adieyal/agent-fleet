"""Seed the sourced Restoke V2 snapshot through Work.

Run: uv run --frozen python scripts/seed_supplier_slice.py --prd /path/to/prd.json
FLEET_STORE selects the destination. The optional PRD is read, never changed.
The adjacent JSON records source paths for every authored field. Titles and parent
paths identify snapshot items; reruns refresh their sourced fields.
"""

import argparse
import json
from pathlib import Path

from fleet.composition import open_work, open_workspace
from fleet.errors import FleetError
from fleet.modules.work import EvidenceSpecification, WorkFacade


def seed(work: WorkFacade, prd: Path | None = None, *, project: str) -> None:
    records = json.loads(Path(__file__).with_suffix(".json").read_text())
    stories = []
    if prd is not None:
        prd = prd.expanduser().resolve()
        stories = json.loads(prd.read_text())["userStories"]
        records.extend({
            "key": story["id"], "parent": "slice6", "kind": "task",
            "title": story["title"], "goal": story["description"],
            "sources": [str(prd)], "condition": "none", "next_step": None,
        } for story in stories)
    existing = work.list(project=project)
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
            item = work.add(project=project, parent=parent, kind=record["kind"],
                            title=record["title"], goal=goal, next_step=record["next_step"],
                            actor="supplier-slice-seed")
        changes = {name: value for name, value in {
            "goal": goal, "condition": record["condition"],
            "next_step": record["next_step"], "resume_condition": None,
        }.items() if getattr(item, name) != value}
        if changes:
            item = work.set(item.id, actor="supplier-slice-seed", **changes)
        identities[record["key"]] = item.id
    criteria = work.criteria(identities["slice6"])
    for story in stories:
        text = story["id"] + " passes"
        reference = f"{prd}#{story['id']}"
        specification = EvidenceSpecification(reference, "passes == true")
        criterion = next((item for item in criteria if item.text == text), None)
        if criterion is None:
            criterion = work.add_criterion(identities["slice6"], text=text,
                verification="checked", specification=specification, actor="supplier-slice-seed")
        elif criterion.specification != specification:
            raise ValueError(f"{text}: evidence source differs from {reference}")
        if criterion.state != "met":
            try:
                work.meet(criterion.id, actor="supplier-slice-seed", evidence=(reference,))
            except ValueError as error:
                print(f"{text}: unmet — {error} ({reference})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prd", type=Path, help="Slice 6 Ralph prd.json to seed and check")
    parser.add_argument("--project", default="Restoke V2", help="registered project (ID, prefix or name)")
    args = parser.parse_args()
    try:
        project = open_workspace().resolve_project(args.project)
    except FleetError as error:
        parser.error(str(error))
    seed(open_work(), args.prd, project=project)
