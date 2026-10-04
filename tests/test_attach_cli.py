import json
import shlex
import subprocess
import sys

from fleet import cli
from fleet.transport import Host, LOCAL_FLEETD_SOURCE


def test_attach_uses_side_by_side_workers_published_command(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_REMOTE_HOME", str(tmp_path / "side by side"))
    monkeypatch.setenv("FLEET_FLEETD_PATH", str(LOCAL_FLEETD_SOURCE))
    host = Host("local", None, python=sys.executable)
    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: host)
    steps = tmp_path / "steps.json"
    steps.write_text('["Reply done"]')
    created = subprocess.run(host.fleetd_command([
        "create", "--id", "attach-job", "--cwd", str(tmp_path),
        "--project", "p", "--description", "attach test", "--agent", "codex",
        "--steps-file", str(steps), "--hold",
    ]), check=True, capture_output=True, text=True, timeout=10)
    published = json.loads(created.stdout)["tmux"]
    assert shlex.split(published)[2] != "fleet"
    executed = []
    monkeypatch.setattr(cli.transport.os, "execvp", lambda executable, argv: executed.append((executable, argv)))

    cli.main(["attach", "local:attach-job"])

    expected = host.shell_command(published, interactive=True)
    assert executed == [(expected[0], expected)]
