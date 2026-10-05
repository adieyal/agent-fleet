"""Bounded JSON-RPC over stdio for a single long-lived Codex app-server."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from fleet.errors import FleetError
from fleet.services.responder import TurnResult, turn_health

from .provisioning import provision


class CodexAppServer:
    def __init__(self, fleet_home: Path, *, auth: Path | None = None,
                 binary: str = "codex", request_timeout: float = 15) -> None:
        self.fleet_home, self.auth, self.binary = fleet_home, auth, binary
        self.request_timeout = request_timeout
        self.process: subprocess.Popen | None = None
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.turn_lock = threading.Lock()
        self.pending: dict[int, queue.Queue] = {}
        self.events: dict[str, queue.Queue] = {}
        self.counter = 0
        self.error: str | None = None
        self.ready = False
        self.closed = False
        self.version: str | None = None
        self.startup_s: float | None = None
        self.last_turn: dict | None = None
        self.stderr: deque[str] = deque(maxlen=12)
        self.readers: list[threading.Thread] = []
        self.warm_thread: str | None = None
        self.environment = None

    def start(self) -> None:
        started = time.monotonic()
        try:
            environment = provision(self.fleet_home, auth=self.auth, binary=self.binary)
            version = subprocess.run([environment.binary, "--version"], env=environment.environ(),
                                     cwd=environment.home, capture_output=True, text=True,
                                     check=True, timeout=self.request_timeout).stdout.strip()
            if not version:
                raise FleetError("codex --version returned no version")
            with self.lock:
                if self.closed:
                    raise FleetError("app-server closed during startup")
                if self.process is not None:
                    raise FleetError("app-server already started")
                self.environment, self.version = environment, version
                self.process = subprocess.Popen(
                    [environment.binary, "app-server"], env=environment.environ(), cwd=environment.home,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", bufsize=1, start_new_session=True)
                self.readers = [threading.Thread(target=self._read, name="codex-rpc", daemon=True),
                                threading.Thread(target=self._read_stderr, name="codex-stderr", daemon=True)]
                for reader in self.readers:
                    reader.start()
            self._request("initialize", {"clientInfo": {"name": "fleet-responder", "version": "0.1.0"}},
                          self.request_timeout)
            self._send({"method": "initialized"})
            self.warm_thread = self.start_thread(timeout=self.request_timeout)
            with self.lock:
                if self.error or self.closed:
                    raise FleetError(self.error or "app-server closed during startup")
                self.ready, self.startup_s = True, time.monotonic() - started
        except (OSError, subprocess.SubprocessError, FleetError) as failure:
            self._fail(f"responder startup failed: {failure}")
            self.close()
            raise FleetError(self.error) from failure

    def _send(self, message: dict) -> None:
        with self.write_lock:
            process = self.process
            if process is None or process.poll() is not None or self.closed:
                raise FleetError(self.error or "app-server is not running")
            try:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as error:
                self._fail(f"app-server write failed: {error}")
                raise FleetError(self.error) from error

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        if timeout <= 0:
            raise FleetError(f"app-server {method} timed out")
        inbox: queue.Queue = queue.Queue()
        with self.lock:
            if self.error or self.closed:
                raise FleetError(self.error or "app-server is closed")
            self.counter += 1
            identity = self.counter
            self.pending[identity] = inbox
        try:
            self._send({"id": identity, "method": method, "params": params})
            try:
                message = inbox.get(timeout=timeout)
            except queue.Empty:
                # The request may have committed remotely. Retire this process,
                # so a late response cannot leave an unknown live turn behind.
                self._fail(f"app-server {method} timed out after {timeout:.3f}s")
                self.close()
                raise FleetError(self.error) from None
            if isinstance(message, FleetError):
                raise message
            if "error" in message:
                raise FleetError(f"app-server {method}: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                self._fail(f"app-server {method} returned invalid result")
                self.close()
                raise FleetError(self.error)
            return result
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise TypeError("JSON-RPC message must be an object")
                if "method" in message:
                    if "id" in message:
                        # Lean replies have no tools. Never grant an unexpected
                        # server-initiated approval, tool or permission request.
                        self._send({"id": message["id"], "error": {
                            "code": -32601, "message": "Fleet responder does not allow server requests"}})
                        raise ValueError(f"unexpected app-server request: {message['method']}")
                    params = message.get("params", {})
                    if not isinstance(params, dict):
                        raise ValueError("notification params must be an object")
                    with self.lock:
                        inbox = self.events.get(params.get("threadId"))
                        if inbox is not None:
                            inbox.put((time.monotonic(), message))
                elif "id" in message:
                    with self.lock:
                        inbox = self.pending.get(message["id"])
                        if inbox is not None:
                            inbox.put(message)
        except (OSError, ValueError, TypeError, FleetError) as error:
            if not self.closed:
                self._fail(f"app-server protocol failed: {error}")
        finally:
            if not self.closed:
                self._fail(f"app-server stdout closed; exit code {self.process.poll()}")

    def _read_stderr(self) -> None:
        for line in self.process.stderr:
            with self.lock:
                self.stderr.append(line.rstrip()[-1000:])

    def _fail(self, error: str) -> None:
        with self.lock:
            self.error = self.error or error
            self.ready = False
            for inbox in (*self.pending.values(), *self.events.values()):
                inbox.put(FleetError(self.error))

    def start_thread(self, *, timeout: float = 15) -> str:
        if self.environment is None:
            raise FleetError("app-server has not started")
        result = self._request("thread/start", {
            "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never",
            "cwd": str(self.environment.home),
        }, timeout)
        identity = result.get("thread", {}).get("id")
        if not isinstance(identity, str) or not identity:
            raise FleetError("app-server thread/start returned no thread id")
        return identity

    def turn(self, thread_id: str, prompt: str, *, output_schema: dict,
             effort: str = "low", timeout: float = 120,
             on_delta: Callable[[str], None] | None = None,
             on_complete: Callable[[TurnResult], None] | None = None) -> TurnResult:
        started = time.monotonic()
        if timeout <= 0 or not self.turn_lock.acquire(timeout=timeout):
            raise FleetError("app-server turn timed out waiting for another turn")
        inbox: queue.Queue = queue.Queue()
        turn_id: str | None = None
        turn_started = False
        completed = False
        try:
            with self.lock:
                if not self.ready or self.error:
                    raise FleetError(self.error or "app-server is not ready")
                self.events[thread_id] = inbox
            response = self._request("turn/start", {"threadId": thread_id, "effort": effort,
                "outputSchema": output_schema, "input": [{"type": "text", "text": prompt}]},
                min(self.request_timeout, timeout - (time.monotonic() - started)))
            turn_started = True
            turn_id = response.get("turn", {}).get("id")
            if not isinstance(turn_id, str) or not turn_id:
                raise FleetError("app-server turn/start returned no turn id")
            start_s = time.monotonic() - started
            first: float | None = None
            fragments: list[str] = []
            final_messages: dict[str, str] = {}
            usage: dict[str, int] | None = None
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise queue.Empty
                event = inbox.get(timeout=remaining)
                if isinstance(event, FleetError):
                    raise event
                observed, message = event
                params, method = message["params"], message["method"]
                event_turn = params.get("turnId") or params.get("turn", {}).get("id")
                if event_turn != turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    delta = params["delta"]
                    if first is None:
                        first = observed - started
                    fragments.append(delta)
                    if on_delta:
                        on_delta(delta)
                elif method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                    item = params["item"]
                    final_messages[item["id"]] = item["text"]
                elif method == "thread/tokenUsage/updated":
                    usage = params["tokenUsage"]["last"]
                elif method == "turn/completed":
                    turn = params["turn"]
                    for item in turn.get("items", []):
                        if item.get("type") == "agentMessage":
                            final_messages[item["id"]] = item["text"]
                    text = "\n".join(final_messages.values()) if final_messages else "".join(fragments)
                    result = TurnResult(thread_id, turn_id, text, turn["status"],
                                        time.monotonic() - started, first, start_s, usage)
                    with self.lock:
                        self.last_turn = turn_health(result)
                    completed = True
                    if on_complete:
                        on_complete(result)
                    if result.status != "completed":
                        raise FleetError(f"app-server turn {result.status}: {turn.get('error')}")
                    return result
        except queue.Empty:
            if turn_id:
                try:
                    self.interrupt(thread_id, turn_id, timeout=min(self.request_timeout, 1))
                except FleetError:
                    pass
            # Retire even after interrupt acknowledgement: completion can arrive
            # later. Supervision restarts with a fresh, known process state.
            self._fail(f"app-server turn timed out after {timeout:.3f}s")
            self.close()
            raise FleetError(self.error) from None
        except BaseException:
            # Callback/protocol failures must not leave an unobserved model turn.
            if turn_started and not completed:
                self._fail("app-server turn abandoned before completion")
                self.close()
            raise
        finally:
            with self.lock:
                self.events.pop(thread_id, None)
            self.turn_lock.release()

    def interrupt(self, thread_id: str, turn_id: str, *, timeout: float = 5) -> None:
        self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout)

    def health(self) -> dict:
        with self.lock:
            alive = self.process is not None and self.process.poll() is None and not self.closed
            return {"alive": alive, "ready": alive and self.ready, "pid": self.process.pid if self.process else None,
                    "codex_version": self.version, "error": self.error,
                    "startup_s": self.startup_s, "last_turn": self.last_turn}

    def close(self) -> None:
        with self.close_lock:
            with self.lock:
                if self.closed:
                    return
                self.closed, self.ready = True, False
                for inbox in (*self.pending.values(), *self.events.values()):
                    inbox.put(FleetError(self.error or "app-server closed"))
                process = self.process
            if process is not None:
                # Codex can be installed through a wrapper which spawns the native
                # binary. This session belongs only to us; retire its descendants as
                # well, even if the wrapper has already died.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=0.5)
                for reader in self.readers:
                    if reader is not threading.current_thread():
                        reader.join(timeout=0.5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
