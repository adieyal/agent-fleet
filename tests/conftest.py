"""A deck server over the recorded fleet in fixtures/restoke.json, shared by HTTP and browser tests.

Tests that change a fleet (moving a project in) start their own with serve_fixture.
"""

import json
from dataclasses import replace
import os
import shutil
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest


from fleet.container import configured_container
from fleet_web.fixture import FixtureLibrary
from fleet_web.server import make_handler
from fleet.api import FleetError

FIXTURE = Path(__file__).parent / "fixtures" / "restoke.json"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shots", default=None, help="a directory browser tests leave screenshots in, for reviewing the look")


BROWSER_FIXTURES = {"page", "browser", "context", "new_context", "persistent_context"}
REAL_CONFIG_DIRECTORY = (Path.home() / ".config" / "fleet").resolve()
_guard_active = False


def _guard_real_config(event: str, args: tuple[Any, ...]) -> None:
    """Reject access before Python opens a live store or config file."""
    if not _guard_active or event not in {"open", "sqlite3.connect", "os.rename", "os.remove", "os.rmdir", "os.mkdir"}:
        return
    paths = args[:2] if event == "os.rename" else args[:1]
    for value in paths:
        if not isinstance(value, (str, bytes, os.PathLike)):
            continue
        name = os.fsdecode(value)
        if event == "sqlite3.connect" and name.startswith("file:"):
            name = unquote(urlsplit(name).path)
        path = Path(name).resolve()
        if path == REAL_CONFIG_DIRECTORY or REAL_CONFIG_DIRECTORY in path.parents:
            raise AssertionError(f"Tests must not access the user's real Fleet config/store: {path}")


def pytest_configure(config: pytest.Config) -> None:
    global _guard_active
    config.addinivalue_line("markers", "browser: requires a Playwright browser; runs on home")
    sys.addaudithook(_guard_real_config)
    _guard_active = True


def pytest_unconfigure(config: pytest.Config) -> None:
    global _guard_active
    _guard_active = False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    from playwright.sync_api import sync_playwright

    browser_items = []
    for item in items:
        if BROWSER_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.browser)
        if item.get_closest_marker("browser") is not None:
            browser_items.append(item)
    if not browser_items:
        return
    with sync_playwright() as playwright:
        installed = {name: Path(getattr(playwright, name).executable_path).is_file()
                     for name in ("chromium", "firefox", "webkit")}
    for item in browser_items:
        name = getattr(item, "callspec", None)
        browser_name = name.params.get("browser_name", "chromium") if name is not None else "chromium"
        if not installed[browser_name]:
            item.add_marker(pytest.mark.skip(reason="Playwright browsers not installed here; browser tests run on home"))
        elif socket.gethostname().split(".")[0] != "home":
            item.add_marker(pytest.mark.skip(reason="Browser tests run on home; browsers must not launch on this host"))


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    """Headless Chromium must not try to initialize WebGL through a forwarded X display."""
    if browser_type_launch_args.get("headless") is False:
        return browser_type_launch_args
    environment = dict(browser_type_launch_args.get("env", os.environ))
    environment.pop("DISPLAY", None)
    return {**browser_type_launch_args, "env": environment}


@pytest.fixture(scope="session")
def empty_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("store-template") / "fleet.db"
    configured_container(path=path).store()
    return path


@pytest.fixture(scope="session", autouse=True)
def isolated_session_paths(tmp_path_factory: pytest.TempPathFactory, empty_store: Path) -> Iterator[None]:
    """Class/session setup happens before the per-test isolation fixture."""
    root = tmp_path_factory.mktemp("fleet-session")
    config = root / "config" / "config.json"
    config.parent.mkdir()
    config.write_text('{"hosts": {}}')
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, path in {
            "FLEET_CONFIG": config,
            "FLEET_STORE": empty_store,
            "FLEET_HOME": root / "fleet-home",
            "FLEET_MANAGEMENT": root / "management",
            "CLAUDE_CONFIG_DIR": root / "claude-config",
        }.items():
            monkeypatch.setenv(name, str(path))
        yield


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, empty_store: Path) -> None:
    # Reconnecting test decks must never read jobs from the user's configured workers.
    transport = configured_container().transport()
    monkeypatch.setattr(transport, "catch_up_jobs", lambda host: [])
    monkeypatch.setattr(transport, "catch_up_sessions", lambda host, since: [])
    # Trace retention tests explicitly restore this helper against their temporary worker.
    monkeypatch.setattr(transport, "keep_run_trace", lambda execution, host, job: None)
    monkeypatch.setattr("fleet_worker.fleetd.SESSION_RECORDS_DIRECTORY", tmp_path / "fleet-home" / "sessions")
    monkeypatch.setattr("fleet_worker.fleetd.REMOVALS_DIRECTORY", tmp_path / "fleet-home" / "removals")

    def no_worker_documents(*args):
        raise FleetError("test worker document transport is not configured")

    # Resolve the implementation through its public provider; integration tests can restore transport.
    monkeypatch.setattr(configured_container().live_state.provides, "fetch_raw", no_worker_documents)
    path = tmp_path / "fleet.db"
    shutil.copyfile(empty_store, path)
    monkeypatch.setenv("FLEET_STORE", str(path))
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "config" / "config.json"))
    config = tmp_path / "config" / "config.json"
    config.parent.mkdir()
    config.write_text('{"hosts": {}}')
    monkeypatch.setenv("FLEET_HOME", str(tmp_path / "fleet-home"))
    monkeypatch.setenv("FLEET_MANAGEMENT", str(tmp_path / "management"))
    # fleetd reads Claude's deny rules; the user's own settings must not decide a test
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    # Run inside a fleet job, the CLI records the job as the actor; a test decides that itself
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)


@pytest.fixture
def project_id():


    return configured_container().initialized_workspace().edit_registry(lambda registry: registry.create('p')).id


@pytest.fixture(scope="session", autouse=True)
def real_config_unchanged() -> Iterator[None]:
    directory = Path.home() / ".config" / "fleet"

    def snapshot() -> dict[Path, tuple[int, int, int]]:
        return {path.relative_to(directory): (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                for path in directory.rglob("*") if path.is_file() and not path.name.startswith("fleet.db")
                for stat in [path.stat()]}

    before = snapshot()
    yield
    assert snapshot() == before, "Tests changed the user's real Fleet config"


@pytest.fixture(scope="session")
def fixture_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


@contextmanager
def serve_fixture(path: Any) -> Iterator[str]:
    """A deck server over a recorded fleet; what the browser changes stays in this server's memory."""
    container = configured_container()
    state = container.fixture_state(fixture=container.fixture_data(path=path)) if isinstance(path, Path) else path
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state, FixtureLibrary(state.fixture, container=state.container)))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def deck_state() -> Any:
    container = configured_container()
    return container.fixture_state(fixture=container.fixture_data(path=FIXTURE))


@pytest.fixture(scope="session")
def base_url(deck_state: Any) -> Iterator[str]:
    with serve_fixture(deck_state) as url:
        yield url


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    """Headless Chromium without a display: an inherited DISPLAY (say a dead SSH X forward) makes WebGL
    fail to start, and the 3D views then never become ready."""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    return {**browser_type_launch_args, "env": env}


@pytest.fixture
def cli_container():
    from fleet.container import Container
    return Container()


@pytest.fixture
def override_cli_method(cli_container):
    from dependency_injector import providers

    def override(name, method, replacement):
        provider = getattr(cli_container, name)
        original = providers.Factory(provider.provides, *provider.args, **provider.kwargs)

        def create():
            service = original()
            setattr(service, method, replacement)
            return service

        provider.override(providers.Factory(create))

    return override


@pytest.fixture
def override_web_store(request, monkeypatch):
    """Rebind a session deck's providers to a test store, restoring them after the test."""
    def override(state, store):
        replacement = configured_container(store)
        keep = {'settings', 'unit', 'transport', 'documents', 'project_documents',
                'project_library', 'overview_cache', 'overview', 'fixture_scope'}
        overridden = []
        for name, provider in state.container.providers.items():
            if name not in keep:
                provider.override(getattr(replacement, name))
                overridden.append(provider)
        request.addfinalizer(lambda: [provider.reset_last_overriding() for provider in reversed(overridden)])
        monkeypatch.setattr(state, 'store', store)
        monkeypatch.setattr(state, 'execution', replacement.execution())
        monkeypatch.setattr(state, 'decisions', replacement.decisions())
        monkeypatch.setattr(state, 'triage_status', replacement.triage_scheduler(deliver=None, host=None).status)
        monkeypatch.setattr(state, 'reads', replace(state.reads, execution=replacement.execution(),
                                                   revision=lambda: (store, store.latest_sequence())))
    return override
