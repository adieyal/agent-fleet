import json

from fleet import cli
from fleet.composition import open_attention, open_work


def test_options_and_answer_and_status(capsys):
    work = open_work()
    item = work.add(project="p", title="Delivery", goal="Ship", actor="author")
    work.set(item.id, condition="blocked", actor="author")
    question = open_attention().raise_item(project="p", work_item=item.id, kind="decision", owner="user",
        source="manual", source_reference="q", headline="Choose route", context_reference="doc:1",
        actor="author", options=("Direct", "Scenic"))
    cli.main(["attention", "list"])
    entries = json.loads(capsys.readouterr().out)
    assert next(entry for entry in entries if entry["id"] == question.id)["options"] == ["Direct", "Scenic"]
    cli.main(["answer", question.id, "2", "--next-step", "Travel"])
    decision = json.loads(capsys.readouterr().out)
    assert decision["answer"] == "Scenic"
    assert decision["actor"] == "user"
    cli.main(["status", "p", "--json"])
    node, = json.loads(capsys.readouterr().out)["work_items"]
    assert node["decisions"] == [decision]
    assert node["condition"] == "none"
    assert node["next_step"] == "Travel"
    cli.main(["status", "p"])
    output = capsys.readouterr().out
    assert output.index("Delivery") < output.index("Choose route") < output.index("Scenic")
    assert "user" in output
