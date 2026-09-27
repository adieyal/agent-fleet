import json
from pathlib import Path
import runpy

from fleet import cli
from fleet.composition import open_store, open_work


SCRIPT = Path(__file__).parents[2] / "scripts/seed_supplier_slice.py"


def seed():
    runpy.run_path(str(SCRIPT), run_name="__main__")


def test_supplier_seed_is_repeatable_and_updates(capsys):
    seed()
    work = open_work()
    before = work.list(project="Restoke V2")
    sequence = open_store().latest_sequence()
    seed()
    assert work.list(project="Restoke V2") == before
    assert open_store().latest_sequence() == sequence
    supplier = next(item for item in before if item.title == "Supplier migration")
    work.set(supplier.id, actor="test", goal="stale imported goal")
    seed()
    assert work.get(supplier.id).goal == supplier.goal
    assert {item.id for item in work.list(project="Restoke V2")} == {item.id for item in before}


def test_supplier_seed_status_and_sources(capsys, monkeypatch):
    original = Path.open

    def no_source_writes(path, mode="r", *args, **kwargs):
        if "restoke" in str(path) or "first-stories/sources" in str(path):
            assert not any(flag in mode for flag in "wax+")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_source_writes)
    seed()
    capsys.readouterr()
    cli.main(["status", "Restoke V2", "--json"])
    tree = json.loads(capsys.readouterr().out)
    epic, = tree["work_items"]
    assert epic["kind"] == "epic"
    supplier, dx = epic["children"]
    assert supplier["title"] == "Supplier migration"
    assert dx["title"] == "Development experience"
    milestones = [item for item in supplier["children"] if item["kind"] == "milestone"]
    assert len([item for item in milestones if item["condition"] == "complete"]) == 6
    active, = [item for item in milestones if item["condition"] != "complete"]
    assert active["title"] == "Slice 6: supplier imports"
    assert active["progress"] == {"basis": "unknown", "complete": None, "total": None}
    assert active["criteria"] == active["children"] == []
    assert active["next_step"] == "Draft the slice 6 PRD and questions"
    tasks = [item for item in supplier["children"] if item["kind"] == "task"]
    assert len(tasks) == 9 + 21 + 47
    assert all(item["condition"] == "none" for item in tasks)
    records = json.loads(SCRIPT.with_suffix(".json").read_text())
    assert all(record["sources"] and all(Path(source).is_absolute() for source in record["sources"]) for record in records)
    assert all("Source: " in item.goal for item in open_work().list(project="Restoke V2"))
    assert not any(item.title == "Invoice analysis" for item in open_work().list(project="Restoke V2"))
    cli.main(["status", "Restoke V2"])
    output = capsys.readouterr().out
    assert "Slice 6: supplier imports" in output and "Progress: unknown" in output
    assert "complete" in output and "Development experience" in output
