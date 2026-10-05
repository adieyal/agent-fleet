"""Live sessions own cancellation and do not leave background workers behind."""
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fleet import transport
from fleet.services.live import start_live
from fleet_web import server


def test_concurrent_starts_share_workers_and_close_joins_them():
    entered = threading.Event()
    stops = []

    def history(stop):
        stops.append(stop)
        entered.set()
        stop.wait()

    state = SimpleNamespace(hosts=[], follow_history=history)
    with ThreadPoolExecutor(max_workers=8) as pool:
        runtimes = list(pool.map(lambda _: start_live(state), range(16)))
    runtime = runtimes[0]
    try:
        assert entered.wait(2)
        assert all(value is runtime for value in runtimes)
        assert stops == [runtime.stop]
        assert len(runtime.threads) == 1
    finally:
        runtime.close()
    runtime.close()
    assert all(not thread.is_alive() for thread in runtime.threads)
    assert start_live(state) is runtime
    assert runtime.stop.is_set()


def test_close_cancels_silent_host_and_reaps_process(monkeypatch):
    processes = []
    launched = threading.Event()
    popen = subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        launched.set()
        return process

    host = SimpleNamespace(name="worker", fleetd_command=lambda _: [sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(transport, "worker_version", lambda host: {"wire_protocol_version": 1})
    monkeypatch.setattr(transport, "ensure_master", lambda _: None)
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    state = SimpleNamespace(hosts=[host], transport=transport, follow_history=lambda stop: stop.wait(),
                            update=Mock())
    runtime = start_live(state)
    try:
        assert launched.wait(2)
    finally:
        runtime.close()
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed and processes[0].stderr.closed
    assert all(not thread.is_alive() for thread in runtime.threads)
    state.update.assert_not_called()


def test_close_interrupts_reconnect_delay(monkeypatch):
    attempted = threading.Event()

    def stream(*args, **kwargs):
        attempted.set()
        return "disconnected"

    state = SimpleNamespace(hosts=[SimpleNamespace(name="worker")],
                            transport=SimpleNamespace(follow_stream=stream),
                            follow_history=lambda stop: stop.wait(), update=Mock())
    monkeypatch.setattr("fleet.services.live.RECONNECT_DELAY", 60)
    runtime = start_live(state)
    try:
        assert attempted.wait(2)
    finally:
        runtime.close()
    assert all(not thread.is_alive() for thread in runtime.threads)


@pytest.mark.parametrize("failure", [None, RuntimeError("listener failed")])
def test_serve_closes_runtime_on_return_and_failure(monkeypatch, failure):
    state, runtime = object(), Mock()
    container = SimpleNamespace(live_state=Mock(return_value=state), start_live=Mock(return_value=runtime))
    monkeypatch.setattr(server, "ProjectLibrary", Mock())
    monkeypatch.setattr(server, "make_handler", Mock())
    monkeypatch.setattr(server, "run_server", Mock(side_effect=failure))
    if failure:
        with pytest.raises(RuntimeError, match="listener failed"):
            server.serve([], port=0, bind="127.0.0.1", container=container)
    else:
        server.serve([], port=0, bind="127.0.0.1", container=container)
    container.start_live.assert_called_once_with(state=state)
    runtime.close.assert_called_once_with()


def test_cancelled_stream_never_connects_or_launches(monkeypatch):
    stop = threading.Event()
    stop.set()
    connect, launch = Mock(), Mock()
    monkeypatch.setattr(transport, "ensure_master", connect)
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    assert transport.follow_stream(object(), Mock(), events="15", silence_limit=20, stop=stop) == "stream cancelled"
    connect.assert_not_called()
    launch.assert_not_called()


def test_run_server_closes_listener_on_failure(monkeypatch):
    listener = Mock()
    listener.serve_forever.side_effect = RuntimeError("listener failed")
    monkeypatch.setattr(server, "ThreadingHTTPServer", Mock(return_value=listener))
    with pytest.raises(RuntimeError, match="listener failed"):
        server.run_server(Mock(), port=0, bind="127.0.0.1", open_browser=False)
    listener.server_close.assert_called_once_with()
