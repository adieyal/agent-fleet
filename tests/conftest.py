"""A deck server over the recorded fleet in fixtures/restoke.json, shared by HTTP and browser tests."""

import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.server import make_handler

FIXTURE = Path(__file__).parent / "fixtures" / "restoke.json"


@pytest.fixture(scope="session")
def fixture_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    state = FixtureState.load(FIXTURE)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, FixtureLibrary(state.fixture)))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
