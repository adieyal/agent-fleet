import json

from fleet.container import configured_container
from fleet import cli


def test_options_and_answer_and_status(capsys, project_id):
    work = configured_container().work()
    item = work.add(project=project_id, title="Delivery", goal="Ship", actor="author")
    work.set(item.id, condition="blocked", actor="author")
    question = configured_container().initialized_attention().raise_item(project=project_id, work_item=item.id, kind='decision', owner='user', source='manual', source_reference='q', headline='Choose route', context_reference='doc:1', actor='author', options=('Direct', 'Scenic'))
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
    assert node["attention"] == []
    assert node["condition"] == "none"
    assert node["next_step"] == "Travel"
    cli.main(["status", "p"])
    output = capsys.readouterr().out
    assert output.index("Delivery") < output.index("Choose route") < output.index("Scenic")
    assert "user" in output
