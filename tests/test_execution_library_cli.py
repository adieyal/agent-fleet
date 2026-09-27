import json
from types import SimpleNamespace

import pytest

from fleet import cli, composition


def test_run_and_library_link_fetch_nothing(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("linking must not fetch anything")

    monkeypatch.setattr(cli.transport, "call", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    item = composition.open_work().add(project="p", title="Task", goal="Ship", actor="user")
    cli.main(["run", "link", "offline", "job", item.id])
    run = json.loads(capsys.readouterr().out)
    cli.main(["run", "link", "offline", "job", item.id])
    assert json.loads(capsys.readouterr().out) == run
    cli.main(["library", "link", "https://example.org/private", "--work-item", item.id])
    entry = json.loads(capsys.readouterr().out)
    assert entry["availability"] == "external"
    assert entry["title"] is None
    assert composition.open_library().list()[0].title is None
    assert entry["canonical_location"] == "https://example.org/private"
    cli.main(["library", "link", "https://example.org/project", "--project", "p"])
    assert json.loads(capsys.readouterr().out)["work_item"] is None


@pytest.mark.parametrize("start_fails", [False, True])
def test_send_links_created_job(monkeypatch, capsys, start_fails):
    item = composition.open_work().add(project="p", title="Task", goal="Ship", actor="user")
    calls = []

    def call(host, arguments, **kwargs):
        calls.append(arguments[0])
        if arguments[0] == "start" and start_fails:
            raise cli.FleetError("start unavailable")
        run, = composition.open_execution().runs()
        if arguments[0] == "create":
            assert arguments[arguments.index("--id") + 1] == run.remote_job_id
        return {"id": run.remote_job_id, "status": "queued", "steps": [{}], "description": "Task"}

    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(cli.transport, "call", call)
    arguments = ["send", "--host", "fake", "--project", "p", "--description", "Task",
                 "--cwd", "/tmp", "--step", "Do it", "--work-item", item.id, "--json"]
    if start_fails:
        with pytest.raises(SystemExit):
            cli.main(arguments)
    else:
        cli.main(arguments)
    run, = composition.open_execution().runs()
    assert (run.host, run.remote_job_id, run.runtime) == ("fake", run.id, "claude")
    assert composition.open_execution().actions()[0].work_item == item.id
    assert calls == ["create", "start"]
