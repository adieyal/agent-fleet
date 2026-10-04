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
    from fleet.services.context import Context
    monkeypatch.setattr(Context, "push", lambda self, host, job, paths: pushed.append(job))
    (tmp_path / "brief.md").write_text("x")
    cli.main(["push", "home:6b0dbd89", str(tmp_path / "brief.md")])
    assert pushed == ["6b0dbd89-d087-4d48-a20f-9a47af709c32"]


def remember(host, identity, *, session=False):
    execution = cli.open_execution()
    if session:
        execution.observe_session(host, {"id": identity, "agent": "codex", "status": "idle"})
    else:
        execution.record_observed(host, {"id": identity, "agent": "codex"})


def test_local_prefix_does_not_list_or_connect_to_worker(home, monkeypatch):
    identity = JOBS["jobs"][0]["id"]
    remember("home", identity)

    def forbidden(*args, **kwargs):
        pytest.fail("locally known job must not contact a worker")
    monkeypatch.setattr(cli.transport, "call", forbidden)
    monkeypatch.setattr(cli.transport, "ensure_master", forbidden)
    assert cli.resolve("home:6b0dbd89") == (home, identity)


def test_host_qualified_full_uuid_does_not_list_worker(home, monkeypatch):
    monkeypatch.setattr(cli.transport, "call", lambda *args, **kwargs: pytest.fail("unexpected listing"))
    identity = JOBS["jobs"][0]["id"]
    assert cli.resolve(f"home:{identity}") == (home, identity)


def test_local_bare_prefix_uses_configured_host(home, monkeypatch):
    remember("home", JOBS["jobs"][0]["id"])
    monkeypatch.setattr(cli.transport, "configured_hosts", lambda: [home])
    monkeypatch.setattr(cli.transport, "gather", lambda *args: pytest.fail("unexpected listing"))
    assert cli.resolve("6b0dbd89") == (home, JOBS["jobs"][0]["id"])


def test_local_ambiguity_is_rejected_without_worker_calls(home, monkeypatch):
    for job in JOBS["jobs"][:2]:
        remember("home", job["id"])
    monkeypatch.setattr(cli.transport, "call", lambda *args, **kwargs: pytest.fail("unexpected listing"))
    with pytest.raises(FleetError, match="matches 2 jobs on home"):
        cli.resolve("home:6b")


def test_local_exact_legacy_id_beats_longer_prefix(home, monkeypatch):
    remember("home", "abc")
    remember("home", "abcdef")
    monkeypatch.setattr(cli.transport, "call", lambda *args, **kwargs: pytest.fail("unexpected listing"))
    assert cli.resolve("home:abc") == (home, "abc")


def test_local_query_scopes_host_and_excludes_sessions(home):
    remember("home", "job-home")
    remember("carbon", "job-carbon")
    remember("home", "session-home", session=True)
    execution = cli.open_execution()
    assert set(execution.job_identities()) == {("home", "job-home"), ("carbon", "job-carbon")}
    assert execution.job_identities("home") == [("home", "job-home")]


def test_local_miss_lists_worker_without_events(home, monkeypatch):
    calls = []
    monkeypatch.setattr(cli.transport, "call", lambda host, arguments: calls.append(arguments) or JOBS)
    assert cli.resolve("home:6b0dbd89")[1] == JOBS["jobs"][0]["id"]
    assert calls == [["ls", "--all", "--events", "0"]]


def test_bare_local_miss_preserves_host_timeout(home, monkeypatch):
    from fleet.transport import HostReport

    monkeypatch.setattr(cli.transport, "configured_hosts", lambda: [home])
    monkeypatch.setattr(cli.transport, "gather", lambda *args: [HostReport(home, [], "home: timed out after 20s")])
    with pytest.raises(FleetError, match="cannot resolve 'unknown': home: timed out after 20s"):
        cli.resolve("unknown")


def test_bare_remote_resolution_rejects_incomplete_listings(home, monkeypatch):
    from fleet.transport import HostReport

    carbon = SimpleNamespace(name="carbon")
    monkeypatch.setattr(cli.transport, "configured_hosts", lambda: [home, carbon])
    monkeypatch.setattr(cli.transport, "gather", lambda *args: [
        HostReport(home, JOBS["jobs"], None), HostReport(carbon, [], "carbon: timed out after 20s")])
    with pytest.raises(FleetError, match="carbon: timed out"):
        cli.resolve("6b0dbd89")


def test_add_retry_resolves_locally_and_reports_command_timeout(home, monkeypatch, capsys):
    identity = JOBS["jobs"][0]["id"]
    remember("home", identity)
    calls = []

    def timeout(host, arguments, **kwargs):
        calls.append(arguments)
        raise FleetError("home: timed out after 30s")
    monkeypatch.setattr(cli.transport, "call", timeout)
    with pytest.raises(SystemExit) as error:
        cli.main(["add", "home:6b0dbd89", "--retry"])
    assert error.value.code == 2
    assert calls == [["add", identity, "--steps-file", "/dev/stdin", "--retry"]]
    assert "home: timed out after 30s" in capsys.readouterr().err


def test_short_id_listing_timeout_is_printed(home, monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise FleetError("home: timed out after 30s")
    monkeypatch.setattr(cli.transport, "call", timeout)
    with pytest.raises(SystemExit) as error:
        cli.main(["show", "home:unknown"])
    assert error.value.code == 2
    assert "cannot resolve 'home:unknown': home: timed out after 30s" in capsys.readouterr().err


def test_untranslated_subprocess_timeout_is_printed(home, monkeypatch, capsys):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 10)
    monkeypatch.setattr(cli.transport, "call", timeout)
    with pytest.raises(SystemExit) as error:
        cli.main(["show", f"home:{JOBS['jobs'][0]['id']}"])
    assert error.value.code == 2
    assert "fleet: show timed out after 10s" in capsys.readouterr().err


@pytest.mark.parametrize("reference", ["", "home:"])
def test_empty_id_does_not_select_an_arbitrary_job(home, reference):
    with pytest.raises(FleetError, match="job id must not be empty"):
        cli.resolve(reference)


def test_bare_local_ambiguity_requires_host(home, monkeypatch):
    carbon = SimpleNamespace(name="carbon")
    for host in (home, carbon):
        remember(host.name, "shared-job")
    monkeypatch.setattr(cli.transport, "configured_hosts", lambda: [home, carbon])
    monkeypatch.setattr(cli.transport, "gather", lambda *args: pytest.fail("unexpected listing"))
    with pytest.raises(FleetError, match="matches 2 jobs; use host:id"):
        cli.resolve("shared")
    assert cli.resolve("home:shared") == (home, "shared-job")


def test_bare_local_miss_can_resolve_from_worker(home, monkeypatch):
    from fleet.transport import HostReport

    monkeypatch.setattr(cli.transport, "configured_hosts", lambda: [home])
    monkeypatch.setattr(cli.transport, "gather", lambda *args: [HostReport(home, JOBS["jobs"], None)])
    assert cli.resolve("6b0dbd89") == (home, JOBS["jobs"][0]["id"])


def test_retained_job_on_unconfigured_host_does_not_resolve(home, monkeypatch):
    remember("removed-host", "gone-job")
    monkeypatch.setattr(cli.transport, "configured_hosts", lambda: [home])
    monkeypatch.setattr(cli.transport, "gather", lambda *args: [])
    with pytest.raises(FleetError, match="matches 0 jobs"):
        cli.resolve("gone")


@pytest.mark.parametrize("command", ["tail", "wait"])
def test_streaming_commands_prepare_connection_before_opening_pipe(home, monkeypatch, command):
    import json
    from io import StringIO

    identity = JOBS["jobs"][0]["id"]
    remember("home", identity)
    home.fleetd_command = lambda arguments: arguments
    order = []
    monkeypatch.setattr(cli.transport, "ensure_master", lambda host: order.append("connect"))
    monkeypatch.setattr(cli.transport, "call", lambda *args, **kwargs: pytest.fail("unexpected listing"))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)

    def spawn(arguments, **kwargs):
        assert arguments[1] == identity
        order.append("pipe")
        output = "" if command == "tail" else json.dumps({"status": "done", "description": "Finished", "results": []})
        return SimpleNamespace(stdout=StringIO(output), poll=lambda: 0)
    monkeypatch.setattr(cli.transport.subprocess, "Popen", spawn)
    if command == "wait":
        with pytest.raises(SystemExit) as error:
            cli.main([command, "home:6b0dbd89"])
        assert error.value.code == 0
    else:
        cli.main([command, "home:6b0dbd89"])
    assert order == ["connect", "pipe"]


def test_wait_prepares_all_hosts_before_launching_any_waiter(monkeypatch):
    first, second = SimpleNamespace(name="home"), SimpleNamespace(name="carbon")
    monkeypatch.setattr(cli, "resolve", lambda reference: (first if reference == "one" else second, "job"))
    order = []

    def prepare(host):
        order.append(host.name)
        if host is second:
            raise FleetError("carbon: SSH control connection check timed out after 10s")
    monkeypatch.setattr(cli.transport, "ensure_master", prepare)
    monkeypatch.setattr(cli.transport.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("waiter launched before preparation completed"))
    with pytest.raises(FleetError, match="carbon: SSH control connection check timed out"):
        cli.wait_for(["one", "two"], step=None, timeout=None, as_json=False)
    assert order == ["home", "carbon"]
