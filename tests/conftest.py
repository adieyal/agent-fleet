"""A deck server over the recorded fleet in fixtures/restoke.json, shared by HTTP and browser tests.

Tests that change a fleet (moving a project in) start their own with serve_fixture.
"""

import json
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from fleet.composition import open_store
from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.server import make_handler

FIXTURE = Path(__file__).parent / "fixtures" / "restoke.json"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shots", default=None, help="a directory browser tests leave screenshots in, for reviewing the look")


@pytest.fixture(scope="session")
def empty_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("store-template") / "fleet.db"
    open_store(path)
    return path


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, empty_store: Path) -> None:
    path = tmp_path / "fleet.db"
    shutil.copyfile(empty_store, path)
    monkeypatch.setenv("FLEET_STORE", str(path))
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "config" / "config.json"))
    monkeypatch.setenv("FLEET_HOME", str(tmp_path / "fleet-home"))


@pytest.fixture(scope="session", autouse=True)
def real_config_unchanged() -> Iterator[None]:
    directory = Path.home() / ".config" / "fleet"

    def snapshot() -> dict[Path, tuple[int, int, int]]:
        return {path.relative_to(directory): (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                for path in directory.rglob("*") if path.is_file()
                for stat in [path.stat()]}

    before = snapshot()
    yield
    assert snapshot() == before, "Tests changed the user's real Fleet config"


@pytest.fixture(scope="session")
def fixture_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


@contextmanager
def serve_fixture(path: Path) -> Iterator[str]:
    """A deck server over a recorded fleet; what the browser changes stays in this server's memory."""
    state = FixtureState.load(path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, FixtureLibrary(state.fixture)))
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
def base_url() -> Iterator[str]:
    with serve_fixture(FIXTURE) as url:
        yield url
