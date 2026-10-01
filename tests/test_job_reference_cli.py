"""A job reference is `host:id` or a bare id, and either id may be a unique prefix."""

from types import SimpleNamespace

import pytest

from fleet import cli
from fleet.errors import FleetError

JOBS = {"jobs": [{"id": "6b0dbd89-d087-4d48-a20f-9a47af709c32"}, {"id": "6b1f00aa-0000-0000-0000-000000000000"},
                 {"id": "a1c3e9"}]}


@pytest.fixture
def home(monkeypatch):
    host = SimpleNamespace(name="home")
    monkeypatch.setattr(cli.transport, "host_by_name", lambda name: host)
    monkeypatch.setattr(cli.transport, "ensure_master", lambda host: None)
    monkeypatch.setattr(cli.transport, "call", lambda host, arguments, **_: JOBS if arguments[0] == "ls" else {})
    return host


@pytest.mark.parametrize("reference, expected", [
    ("home:6b0dbd89", "6b0dbd89-d087-4d48-a20f-9a47af709c32"),
    ("home:6b0dbd89-d087-4d48-a20f-9a47af709c32", "6b0dbd89-d087-4d48-a20f-9a47af709c32"),
    ("home:a1c3e9", "a1c3e9")])
def test_a_host_qualified_prefix_resolves_to_the_full_id(home, reference, expected):
    assert cli.resolve(reference) == (home, expected)


@pytest.mark.parametrize("prefix, count", [("6b", 2), ("ffff", 0)])
def test_an_ambiguous_or_unknown_prefix_is_refused(home, prefix, count):
    with pytest.raises(FleetError, match=f"matches {count} jobs on home"):
        cli.resolve(f"home:{prefix}")


def test_push_with_a_short_id_copies_into_the_full_jobs_context(home, monkeypatch, tmp_path):
    pushed = []
    monkeypatch.setattr(cli, "push_context", lambda host, job, paths: pushed.append(job))
    (tmp_path / "brief.md").write_text("x")
    cli.main(["push", "home:6b0dbd89", str(tmp_path / "brief.md")])
    assert pushed == ["6b0dbd89-d087-4d48-a20f-9a47af709c32"]
