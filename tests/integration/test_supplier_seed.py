import json
from pathlib import Path
import runpy
import sys

import pytest

from fleet.container import configured_container
from fleet_cli import cli


SCRIPT = Path(__file__).parents[2] / "scripts/seed_supplier_slice.py"
# Story fields copied from first-stories/sources/v2-suppliers-slice6/prd.json.


@pytest.fixture(autouse=True)
def supplier_project():
    return configured_container().initialized_workspace().edit_registry(lambda registry: registry.create('Restoke V2')).id


def seed(prd=None):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "argv", [str(SCRIPT)] + (["--prd", str(prd)] if prd else []))
        runpy.run_path(str(SCRIPT), run_name="__main__")


def test_supplier_seed_is_repeatable_and_updates(capsys, supplier_project):
    seed()
    work = configured_container().work()
    before = work.list(project=supplier_project)
    sequence = configured_container().store().latest_sequence()
    seed()
    assert work.list(project=supplier_project) == before
    assert configured_container().store().latest_sequence() == sequence
    supplier = next(item for item in before if item.title == "Supplier migration")
    work.set(supplier.id, actor="test", goal="stale imported goal")
    seed()
    assert work.get(supplier.id).goal == supplier.goal
    assert {item.id for item in work.list(project=supplier_project)} == {item.id for item in before}


def test_supplier_seed_status_and_sources(capsys, monkeypatch, supplier_project):
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
    assert active["next_step"] == "Answer slice 6 questions.md and start its loop"
    tasks = [item for item in supplier["children"] if item["kind"] == "task"]
    assert len(tasks) == 9 + 21 + 47
    assert all(item["condition"] == "none" for item in tasks)
    records = json.loads(SCRIPT.with_suffix(".json").read_text())
    assert all(record["sources"] and all(Path(source).is_absolute() for source in record["sources"]) for record in records)
    assert all(('Source: ' in item.goal for item in configured_container().work().list(project=supplier_project)))
    assert not any((item.title == 'Invoice analysis' for item in configured_container().work().list(project=supplier_project)))
    cli.main(["status", "Restoke V2"])
    output = capsys.readouterr().out
    assert "Slice 6: supplier imports" in output and "Progress: unknown" in output
    assert "complete" in output and "Development experience" in output


def test_slice6_prd_progress_and_repeatability(tmp_path, capsys, supplier_project):
    prd = tmp_path / "prd.json"
    content = json.loads((SCRIPT.parents[1] / "tests/fixtures/supplier_slice6_prd.json").read_text())
    prd.write_text(json.dumps(content))
    seed()
    work = configured_container().work()
    original = work.list(project=supplier_project)
    seed(prd)
    milestone = next(item for item in work.list() if item.title == "Slice 6: supplier imports")
    tasks = [item for item in work.list() if item.parent == milestone.id]
    assert len(tasks) == 15
    assert {item.title: item.goal for item in tasks} == {
        story["title"]: story["description"] + "\nSource: " + str(prd)
        for story in content["userStories"]
    }
    assert all(item.kind == "task" for item in tasks)
    assert all(work.get(item.id) == item for item in original)
    criteria = work.criteria(milestone.id)
    assert len(criteria) == 15
    assert {item.text for item in criteria} == {story["id"] + " passes" for story in content["userStories"]}
    assert all(item.verification == "checked" and item.specification.result == "passes == true"
               and item.specification.reference.startswith(str(prd) + "#") for item in criteria)
    for count in (0, 2):
        if count:
            for story in content["userStories"][:2]:
                story["passes"] = True
            prd.write_text(json.dumps(content))
            seed(prd)
        capsys.readouterr()
        cli.main(["status", "Restoke V2", "--json"])
        tree = json.loads(capsys.readouterr().out)
        supplier = tree["work_items"][0]["children"][0]
        active = next(item for item in supplier["children"] if item["id"] == milestone.id)
        assert active["progress"] == {"basis": "criteria", "complete": count, "total": 15}
        cli.main(["status", "Restoke V2"])
        assert f"Progress: {count}/15" in capsys.readouterr().out
        before = work.list()
        criteria = work.criteria(milestone.id)
        sequence = configured_container().store().latest_sequence()
        seed(prd)
        assert work.list() == before
        assert work.criteria(milestone.id) == criteria
        assert configured_container().store().latest_sequence() == sequence


@pytest.mark.parametrize("case, reason", [
    ("false", "evidence does not match"), ("other-story", "evidence does not match"),
    ("missing-story", "US-091.*missing"), ("missing-file", "file.*missing"),
    ("missing-passes", "passes.*missing"), ("string", "evidence does not match"),
])
def test_prd_criterion_requires_named_true_story(tmp_path, case, reason):
    from fleet.modules.work import EvidenceSpecification

    prd = tmp_path / "prd.json"
    story = {"id": "US-091", "passes": False}
    stories = [story, {"id": "US-092", "passes": True}]
    if case == "missing-story":
        stories.remove(story)
    elif case == "missing-passes":
        del story["passes"]
    elif case == "string":
        story["passes"] = "true"
    if case != "missing-file":
        prd.write_text(json.dumps({"userStories": stories}))
    work = configured_container().work()
    item = work.add(project="test", title="Slice", goal="Import", actor="test")
    reference = str(prd) + "#US-091"
    criterion = work.add_criterion(item.id, text="US-091 passes", verification="checked",
        specification=EvidenceSpecification(reference, "passes == true"), actor="test")
    with pytest.raises(ValueError, match=reason):
        work.meet(criterion.id, actor="test", evidence=(reference,))
    assert work.criteria(item.id)[0].state == "unmet"
