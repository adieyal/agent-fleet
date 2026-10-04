import json
import subprocess

from fleet.container import configured_container
from fleet import cli, transport

from fleet.projections.project import project_status


def test_phase1_status_after_reopening_store_without_hosts(monkeypatch, capsys, tmp_path, project_id):
    store = configured_container().store()
    repo = tmp_path / 'management'
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True, timeout=10)
    configured_container(store).records().register(project_id, repo, actor='user')
    work, attention = configured_container(store).work(), configured_container(store).initialized_attention()
    epic = work.add(project=project_id, title="Supplier slice", goal="Migrate suppliers", kind="epic",
                    next_step="Choose mapping", actor="user")
    milestone = work.add(project=project_id, title="Mapping", goal="Map fields", kind="milestone",
                         parent=epic.id, actor="user")
    work.set(milestone.id, condition="blocked", actor="user")
    work.add_criterion(milestone.id, text="User approves", verification="accepted", actor="user")
    work.set_summary(epic.id, purpose="Migration", done="Inventory", doing="Mapping",
                     next="Review", authoring_role="user", actor="user")
    attention.raise_item(project=project_id, kind="decision", owner="user", source="manual", source_reference="q",
                         headline="Which supplier mapping?", context_reference="work:"+epic.id,
                         actor="user", work_item=epic.id)
    before = store.history_after(0)
    del work, attention, store

    def no_hosts(*args, **kwargs):
        raise AssertionError("status must not contact hosts or load host configuration")

    monkeypatch.setattr(transport, "load_config", no_hosts)
    monkeypatch.setattr(transport, "call", no_hosts)
    reopened = configured_container().store()
    expected = project_status(project_id, configured_container(reopened).work(), configured_container(reopened).initialized_attention(), configured_container(reopened).execution(), configured_container(reopened).library(), configured_container(reopened).decisions())
    cli.main(["status", "p", "--json"])
    assert json.loads(capsys.readouterr().out) == expected
    cli.main(["status", "p"])
    output = capsys.readouterr().out
    for value in ("Migrate suppliers", "Progress: 0/1 milestones", "Choose mapping",
                  "Which supplier mapping?", "blocked", "blocker", "User approves", "accepted",
                  "Inventory", "Mapping", "Review"):
        assert value in output
    assert output.index("blocker") > output.index(f"  milestone {milestone.id[:8]}: Mapping")
    assert reopened.history_after(0) == before
