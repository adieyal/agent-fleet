"""CLI detail and scoped reads, using conftest's isolated Fleet paths."""
import json
from datetime import datetime, timezone

from uuid import uuid4

import pytest

from fleet import cli, composition
from fleet.modules.work.domain import EvidenceSpecification


def read(capsys, *args):
    cli.main(list(args))
    return capsys.readouterr().out


@pytest.fixture
def tree(project_id):
    work = composition.open_work()
    root = work.add(project=project_id, title="Root", goal="Deliver", kind="epic", actor="test")
    child = work.add(project=project_id, title="Child", goal="Read", parent=root.id, actor="test")
    leaf = work.add(project=project_id, title="Leaf", goal="Verify", parent=child.id, actor="test")
    work.add_criterion(child.id, text="Readable", verification="checked", actor="test",
                       specification=EvidenceSpecification("/tmp/cli-read-evidence"))
    return root, child, leaf


def raise_attention(project, work_item=None):
    return composition.open_attention().raise_item(project=project, work_item=work_item, kind="decision",
        owner="agent", source="manual", source_reference=str(uuid4()), headline="Review", context_reference="test",
        actor="test")


def test_work_show_detail_and_json(capsys, tree):
    root, child, leaf = tree
    run = composition.open_execution().link("home", "sample-job", child.id, actor="test", runtime="codex")
    composition.open_decisions().record_guided(child.id, actor="test", question="Which read?",
        answer="Full details", principle="Nothing is hidden")
    entry = raise_attention(root.project, child.id)
    composition.open_attention().resolve(entry.id, details="handled", actor="test")
    output = read(capsys, "work", "show", child.id[:8])
    assert "Parent chain: Root" in output
    assert "Goal: Read" in output and "condition: none" in output
    assert "Next step: not recorded" in output
    assert "Readable" in output and "Review" in output
    assert "sample-job" in output and "Which read?" in output
    assert f"{leaf.id}: Leaf (none)" in output
    result = json.loads(read(capsys, "work", "show", child.id, "--json"))
    assert result["runs"][0]["id"] == run.id
    assert result["decisions"][0]["answer"] == "Full details"
    assert result["parents"][0]["id"] == root.id
    assert result["children"][0]["id"] == leaf.id
    assert result["attention"][0]["state"] == "resolved"
    assert {"runs", "decisions", "criteria", "created", "updated"} <= result.keys()


def test_status_scope_depth_and_open(capsys, tree):
    root, child, leaf = tree
    scoped = json.loads(read(capsys, "status", root.project, "--item", child.id[:8], "--json"))
    assert scoped["work_items"][0]["id"] == child.id
    assert scoped["work_items"][0]["children"][0]["id"] == leaf.id
    shallow = json.loads(read(capsys, "status", root.project, "--depth", "0", "--json"))
    assert shallow["work_items"][0]["children"] == []
    combined = json.loads(read(capsys, "status", root.project, "--item", child.id[:8],
                               "--depth", "0", "--open", "--json"))
    assert combined["work_items"][0]["id"] == child.id
    assert combined["work_items"][0]["children"] == []
    composition.open_work().set(child.id, actor="test", condition="complete")
    opened = json.loads(read(capsys, "status", root.project, "--open", "--json"))
    assert opened["work_items"][0]["children"][0]["id"] == leaf.id
    assert child.id not in [item["id"] for item in opened["work_items"][0]["children"]]
    default = json.loads(read(capsys, "status", root.project, "--json"))
    assert default["work_items"][0]["children"][0]["id"] == child.id


def test_status_unlinked_heading(capsys, tree):
    root, _, _ = tree
    raise_attention(root.project)
    output = read(capsys, "status", root.project)
    assert "Unlinked attention:\n  Attention" in output
    scoped = json.loads(read(capsys, "status", root.project, "--item", root.id, "--json"))
    assert scoped["attention"] == []


def test_attention_list_default_all_and_state(capsys, project_id):
    attention = composition.open_attention()
    entries = [raise_attention(project_id) for _ in range(4)]
    attention.acknowledge(entries[1].id, actor="test")
    attention.snooze(entries[2].id, until=datetime(2099, 1, 1, tzinfo=timezone.utc), actor="test")
    attention.resolve(entries[3].id, details="handled", actor="test")
    assert {i["state"] for i in json.loads(read(capsys, "attention", "list"))} == {"open", "acknowledged", "snoozed"}
    assert len(json.loads(read(capsys, "attention", "list", "--all"))) == 4
    assert [i["state"] for i in json.loads(read(capsys, "attention", "list", "--state", "resolved"))] == ["resolved"]


def test_invalid_scope_and_depth(capsys, tree, project_id):
    other = composition.open_workspace().edit_registry(lambda r: r.create("Other"))
    for args in [("status", other.id, "--item", tree[0].id),
                 ("status", project_id, "--depth", "-1"), ("work", "show", "missing")]:
        with pytest.raises(SystemExit) as raised:
            read(capsys, *args)
        assert raised.value.code == 2


def test_filtered_empty_state(capsys, tree):
    root, child, leaf = tree
    for item in (root, child, leaf):
        composition.open_work().set(item.id, actor="test", condition="complete")
    assert "No work items match the filters." in read(capsys, "status", root.project, "--open")
