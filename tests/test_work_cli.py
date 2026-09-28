import json
import subprocess

import pytest

from fleet.cli import main


def test_work_commands_end_to_end(tmp_path, capsys, project_id):
    repo = tmp_path / 'management'
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True, timeout=10)
    main(['project', 'management', project_id, str(repo)])
    def run(*args):
        main(list(args))
        return json.loads(capsys.readouterr().out)

    epic = run("work", "add", "Epic", "--project", "p", "--kind", "epic", "--goal", "Ship", "--actor", "user")
    task = run("work", "add", "Task", "--project", "p", "--goal", "Check", "--actor", "user")
    assert run("work", "move", task["id"], "--parent", epic["id"], "--actor", "user")["parent"] == epic["id"]
    run("work", "relate", task["id"], epic["id"], "--actor", "user")
    run("work", "set", task["id"], "--condition", "waiting", "--resume-condition", "Data", "--actor", "user")
    assert run("work", "ready", task["id"], "--actor", "user")["condition"] == "ready for review"
    evidence = tmp_path / "result.json"
    evidence.write_text('{"result": "passed"}')
    criterion = run("criterion", "add", task["id"], "Tests pass", "--verification", "checked",
                    "--evidence-reference", str(evidence), "--required-result", "passed", "--actor", "user")
    with pytest.raises(SystemExit) as error:
        main(["criterion", "meet", criterion["id"], "--actor", "user"])
    assert error.value.code == 2
    assert "evidence" in capsys.readouterr().err
    assert run("criterion", "meet", criterion["id"], "--evidence", str(evidence), "--actor", "user")["state"] == "met"
    assert run("summary", "set", epic["id"], "--purpose", "Ship", "--done", "Checked", "--doing", "Review",
               "--next", "Accept", "--authoring-role", "user", "--actor", "user")["purpose"] == "Ship"
