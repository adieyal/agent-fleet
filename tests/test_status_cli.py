import json

from fleet import cli


def test_json_emits_projection_unchanged(monkeypatch, capsys, project_id):
    projection = {"project": "p", "work_items": [], "attention": [], "future": {"unknown": None}}
    monkeypatch.setattr(cli, "project_status", lambda *args: projection)
    cli.main(["status", "p", "--json"])
    assert json.loads(capsys.readouterr().out) == projection


def test_unknown_text_never_renders_as_zero_or_percentage(monkeypatch, capsys, project_id):
    monkeypatch.setattr(cli, "project_status", lambda *args: {
        "project": "p", "attention": [], "work_items": [{
            "title": "Investigation", "kind": "task", "goal": "Find cause", "condition": "none",
            "next_step": None, "plan": None, "resume_condition": None, "criteria": [], "summary": None,
            "attention": [], "children": [], "decisions": [], "relations": [],
            "runs": [], "library": [], "no_follow_up_yet": False,
            "progress": {"basis": "unknown", "complete": None, "total": None}}]})
    cli.main(["status", "p"])
    output = capsys.readouterr().out
    assert "Progress: unknown" in output
    assert "Next step: not recorded" in output
    assert "0" not in output and "%" not in output
