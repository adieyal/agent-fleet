"""fleet attention add addresses a file context as fleet://<host>/<absolute path>, so the deck can open it."""
from __future__ import annotations

from pathlib import Path

import fleet.cli as cli
from fleet.transport import Host


def local_hosts(monkeypatch) -> None:
    monkeypatch.setattr(cli.transport, "configured_hosts", lambda: [Host("home", "home"), Host("carbon", None)])


def test_a_relative_file_becomes_an_address_on_this_host(tmp_path: Path, monkeypatch) -> None:
    local_hosts(monkeypatch)
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "VERIFICATION.md").write_text("# Verdict")
    monkeypatch.chdir(tmp_path)
    assert cli.located_context("outbox/VERIFICATION.md") == f"fleet://carbon{tmp_path.resolve()}/outbox/VERIFICATION.md"


def test_a_relative_file_resolves_in_the_calling_jobs_directory(tmp_path: Path, monkeypatch) -> None:
    local_hosts(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FLEET_JOB_ID", "job-1")
    outbox = tmp_path / ".fleet" / "jobs" / "job-1" / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "REPORT.md").write_text("report")
    monkeypatch.chdir(tmp_path)
    assert cli.located_context("outbox/REPORT.md") == f"fleet://carbon{outbox.resolve()}/REPORT.md"


def test_other_references_are_kept_and_unresolved_paths_warned(tmp_path: Path, monkeypatch, capsys) -> None:
    local_hosts(monkeypatch)
    monkeypatch.chdir(tmp_path)
    for kept in ("fleet://home/x/REPORT.md", "session:home:abc", "job:carbon:1", "Review the route"):
        assert cli.located_context(kept) == kept
    assert capsys.readouterr().err == ""
    assert cli.located_context("outbox/MISSING.md") == "outbox/MISSING.md"
    assert "deck will show it as text only" in capsys.readouterr().err
