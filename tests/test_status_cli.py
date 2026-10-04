import json

from dependency_injector import providers

from fleet import cli


def test_json_emits_projection_unchanged(cli_container, capsys, project_id):
    projection = {"project": "p", "work_items": [], "attention": [], "future": {"unknown": None}}
    cli_container.project_status.override(providers.Factory(lambda **kwargs: projection))
    cli.main(["status", "p", "--json"], container=cli_container)
    assert json.loads(capsys.readouterr().out) == projection


def test_unknown_text_never_renders_as_zero_or_percentage(cli_container, capsys, project_id):
    cli_container.project_status.override(providers.Factory(lambda **kwargs: {
        "project": "p", "attention": [], "work_items": [{
            "id": "abcdefgh-full-id", "title": "Investigation", "kind": "task", "goal": "Find cause", "condition": "none",
            "next_step": None, "plan": None, "resume_condition": None, "criteria": [], "summary": None,
            "attention": [], "children": [], "decisions": [], "relations": [],
            "runs": [], "library": [], "no_follow_up_yet": False,
            "progress": {"basis": "unknown", "complete": None, "total": None}}]}))
    cli.main(["status", "p"], container=cli_container)
    output = capsys.readouterr().out
    assert "Progress: unknown" in output
    assert "Next step: not recorded" in output
    assert "0" not in output and "%" not in output
