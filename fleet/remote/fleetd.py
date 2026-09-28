#!/usr/bin/env python3
"""fleetd — runs Claude Code / Codex jobs on this machine and reports on them.

Stdlib only; copied to each machine by `fleet install`. The local `fleet` CLI
drives it over ssh. Every command that returns data prints one JSON document.

State lives in ~/.fleet:
    config.json                  agent binaries and PATH captured at install
    jobs/<id>/job.json           job definition + ordered steps (the task list)
    jobs/<id>/events.jsonl       normalised activity events
    jobs/<id>/raw-<step>.jsonl   the agent's own JSON stream per step
    jobs/<id>/context/           files pushed by the orchestrator
    jobs/<id>/outbox/            files the agent leaves for the orchestrator
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, List, Optional, Tuple

FLEET_HOME = Path(os.environ.get("FLEET_HOME", Path.home() / ".fleet")).expanduser().resolve()
JOBS_DIRECTORY = FLEET_HOME / "jobs"
CONFIG_PATH = FLEET_HOME / "config.json"
CLAUDE_PROJECTS_DIRECTORY = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
CODEX_SESSIONS_DIRECTORY = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
TMUX_PREFIX = "fleet-"
# A private tmux server without the user's config: personal configs can take seconds to load.
TMUX_SOCKET = ("fleet" if FLEET_HOME == (Path.home() / ".fleet").resolve() else
               "fleet-" + hashlib.sha256(str(FLEET_HOME).encode()).hexdigest()[:16])
TMUX_COMMAND = ["tmux", "-L", TMUX_SOCKET, "-f", "/dev/null"]
SUMMARY_LENGTH = 160
TERMINAL_STATUSES = ("done", "failed", "cancelled", "lost")
STREAM_PROTOCOL_VERSION = 3
DISPATCH_SCHEMA_VERSION = 4
USAGE_SCHEMA_VERSION = 1

JsonObject = Dict[str, Any]


# ---------------------------------------------------------------- utilities


def now() -> float:
    return round(time.time(), 3)


def emit(document: Any) -> None:
    json.dump(document, sys.stdout)
    sys.stdout.write("\n")


def fail(message: str, code: int = 1) -> None:
    emit({"error": message})
    sys.exit(code)


def shorten(text: Any, length: int = SUMMARY_LENGTH) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= length else flat[: length - 1] + "…"


def load_config() -> JsonObject:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def job_directory(job_id: str) -> Path:
    directory = JOBS_DIRECTORY / job_id
    if not (directory / "job.json").exists():
        fail(f"no such job: {job_id}")
    return directory


@contextlib.contextmanager
def locked_job(job_id: str) -> Iterator[JsonObject]:
    """Read-modify-write job.json under an exclusive lock."""
    directory = job_directory(job_id)
    with open(directory / ".lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        job = json.loads((directory / "job.json").read_text())
        before = json.dumps(job, sort_keys=True)
        yield job
        if json.dumps(job, sort_keys=True) == before:
            return
        job["updated_at"] = now()
        temporary_path = directory / "job.json.tmp"
        temporary_path.write_text(json.dumps(job, indent=1))
        temporary_path.replace(directory / "job.json")


def read_job(job_id: str) -> JsonObject:
    return json.loads((job_directory(job_id) / "job.json").read_text())


def append_event(job_id: str, event: JsonObject) -> None:
    event.setdefault("ts", now())
    with open(JOBS_DIRECTORY / job_id / "events.jsonl", "a") as events_file:
        events_file.write(json.dumps(event) + "\n")


def tail_lines(path: Path, count: int) -> List[str]:
    if not path.exists() or count <= 0:
        return []
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        block = min(size, 4096 * max(4, count))
        handle.seek(size - block)
        lines = handle.read().decode(errors="replace").splitlines()
    if block < size:
        lines = lines[1:]
    return lines[-count:]


def read_events(job_id: str, count: int) -> List[JsonObject]:
    events = []
    for line in tail_lines(JOBS_DIRECTORY / job_id / "events.jsonl", count):
        with contextlib.suppress(ValueError):
            events.append(json.loads(line))
    return events


def tmux_session(job_id: str) -> str:
    return TMUX_PREFIX + job_id


def runner_alive(job: JsonObject) -> bool:
    return process_alive(job.get("runner_pid"))


def process_alive(process_id: Optional[int]) -> bool:
    if not process_id:
        return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def derive_status(job: JsonObject) -> str:
    if job.get("cancelled"):
        return "cancelled"
    steps = job["steps"]
    if any(step.get("reason") == "lost" for step in steps):
        return "lost"
    if any(step["status"] == "running" for step in steps):
        if runner_alive(job):
            return "running"
        if job.get("agent_pid") is not None and not process_alive(job["agent_pid"]):
            return "lost"
        return "stalled"
    if any(step["status"] == "failed" for step in steps):
        return "failed"
    if any(step["status"] == "pending" for step in steps):
        return "queued"
    return "done"


# ------------------------------------------------------ event normalisation


MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")
# A step's final reply counts as a document only when it is a real write-up, not "OK".
REPORT_MINIMUM_BYTES = 400
DOCUMENT_READ_LIMIT = 2 * 1024 * 1024


def is_markdown(path: str) -> bool:
    return path.lower().endswith(MARKDOWN_SUFFIXES)


TOOL_KINDS = {
    "Bash": "bash", "BashOutput": "bash", "KillShell": "bash",
    "Edit": "edit", "MultiEdit": "edit", "Write": "edit", "NotebookEdit": "edit",
    "Read": "read", "Glob": "search", "Grep": "search", "LS": "search",
    "WebFetch": "web", "WebSearch": "web",
    "Task": "delegate", "Agent": "delegate",
    "TodoWrite": "plan", "TaskCreate": "plan", "TaskUpdate": "plan",
}


def describe_claude_tool(name: str, tool_input: JsonObject) -> str:
    for key in ("command", "file_path", "pattern", "url", "query", "description", "subject", "prompt"):
        if tool_input.get(key):
            return shorten(tool_input[key])
    return shorten(json.dumps(tool_input))


class ClaudeParser:
    """Turns `claude -p --output-format stream-json` lines into fleet events."""

    def __init__(self) -> None:
        self.todos: Dict[str, JsonObject] = {}
        self.pending_task_subjects: Dict[str, str] = {}

    def parse(self, record: JsonObject) -> List[JsonObject]:
        record_type = record.get("type")
        events: List[JsonObject] = []
        if record_type == "system" and record.get("subtype") == "init":
            events.append({"kind": "session", "session_id": record.get("session_id"),
                           "model": record.get("model")})
        elif record_type == "assistant":
            for block in record.get("message", {}).get("content", []):
                block_type = block.get("type")
                if block_type == "thinking":
                    events.append({"kind": "tool", "tool": "think", "summary": shorten(block.get("thinking")) or "thinking…"})
                elif block_type == "text" and block.get("text", "").strip():
                    events.append({"kind": "text", "summary": shorten(block["text"])})
                elif block_type == "tool_use":
                    events.extend(self._tool_use(block))
        elif record_type == "user":
            events.extend(self._tool_results(record))
        elif record_type == "result":
            events.append({
                "kind": "result",
                "ok": not record.get("is_error") and record.get("subtype") == "success",
                "summary": shorten(record.get("result"), 400),
                "text": record.get("result"),
                "session_id": record.get("session_id"),
                "cost_usd": record.get("total_cost_usd"),
            })
        return events

    def _tool_use(self, block: JsonObject) -> List[JsonObject]:
        name = block.get("name", "")
        tool_input = block.get("input") or {}
        events = [{"kind": "tool", "tool": TOOL_KINDS.get(name, "other"), "name": name,
                   "summary": describe_claude_tool(name, tool_input)}]
        if name == "Bash" and tool_input.get("description"):
            events[0]["intent"] = shorten(tool_input["description"])  # Claude's own words for the command
        if name in ("Write", "Edit", "MultiEdit") and is_markdown(tool_input.get("file_path", "")):
            events[0]["paths"] = [tool_input["file_path"]]
        if name == "TodoWrite":
            self.todos = {str(index): {"text": todo.get("content", ""), "status": todo.get("status", "pending")}
                          for index, todo in enumerate(tool_input.get("todos", []))}
            events.append(self._todo_event())
        elif name == "TaskCreate":
            self.pending_task_subjects[block.get("id", "")] = tool_input.get("subject", "")
        elif name == "TaskUpdate" and str(tool_input.get("taskId")) in self.todos:
            todo = self.todos[str(tool_input["taskId"])]
            todo["status"] = tool_input.get("status", todo["status"])
            if tool_input.get("subject"):
                todo["text"] = tool_input["subject"]
            events.append(self._todo_event())
        return events

    def _tool_results(self, record: JsonObject) -> List[JsonObject]:
        events: List[JsonObject] = []
        tool_use_result = record.get("tool_use_result")
        for block in record.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id", "")
            if tool_use_id in self.pending_task_subjects and isinstance(tool_use_result, dict):
                task = tool_use_result.get("task") or {}
                subject = self.pending_task_subjects.pop(tool_use_id)
                if task.get("id"):
                    self.todos[str(task["id"])] = {"text": task.get("subject", subject), "status": "pending"}
                    events.append(self._todo_event())
            if block.get("is_error"):
                events.append({"kind": "error", "summary": shorten(block.get("content"))})
        return events

    def _todo_event(self) -> JsonObject:
        return {"kind": "todos", "todos": list(self.todos.values())}


class CodexParser:
    """Turns `codex exec --json` lines into fleet events."""

    def parse(self, record: JsonObject) -> List[JsonObject]:
        record_type = record.get("type", "")
        if record_type == "thread.started":
            return [{"kind": "session", "session_id": record.get("thread_id")}]
        if record_type == "turn.failed":
            return [{"kind": "error", "summary": shorten((record.get("error") or {}).get("message"))}]
        if record_type == "error":
            return [{"kind": "error", "summary": shorten(record.get("message"))}]
        if record_type not in ("item.started", "item.updated", "item.completed"):
            return []
        item = record.get("item") or {}
        item_type = item.get("type")
        finished = record_type == "item.completed"
        if item_type == "agent_message" and finished:
            return [{"kind": "text", "summary": shorten(item.get("text")), "text": item.get("text")}]
        if item_type == "reasoning" and finished:
            return [{"kind": "tool", "tool": "think", "summary": shorten(item.get("text")) or "thinking…"}]
        if item_type == "command_execution" and record_type == "item.started":
            return [{"kind": "tool", "tool": "bash", "name": "shell", "summary": shorten(item.get("command"))}]
        if item_type == "command_execution" and finished and item.get("exit_code") not in (0, None):
            return [{"kind": "error", "summary": shorten(f"exit {item.get('exit_code')}: {item.get('command')}")}]
        if item_type == "file_change" and finished:
            changed = [change.get("path", "") for change in item.get("changes", []) if change.get("kind") != "delete"]
            event = {"kind": "tool", "tool": "edit", "name": "apply_patch", "summary": shorten(", ".join(changed))}
            markdown_paths = [path for path in changed if is_markdown(path)]
            if markdown_paths:
                event["paths"] = markdown_paths
            return [event]
        if item_type == "mcp_tool_call" and record_type == "item.started":
            return [{"kind": "tool", "tool": "other", "name": item.get("tool", "mcp"),
                     "summary": shorten(f"{item.get('server')}.{item.get('tool')}")}]
        if item_type == "web_search" and record_type == "item.started":
            return [{"kind": "tool", "tool": "web", "name": "web_search", "summary": shorten(item.get("query"))}]
        if item_type == "todo_list":
            todos = [{"text": todo.get("text", ""), "status": "completed" if todo.get("completed") else "pending"}
                     for todo in item.get("items", [])]
            first_open = next((todo for todo in todos if todo["status"] == "pending"), None)
            if first_open is not None:
                first_open["status"] = "in_progress"
            return [{"kind": "todos", "todos": todos}]
        if item_type == "error":
            return [{"kind": "error", "summary": shorten(item.get("message"))}]
        return []


# ---------------------------------------------------------------- the runner


def job_preamble(job: JsonObject) -> str:
    directory = JOBS_DIRECTORY / job["id"]
    return (
        f"You are running as fleet job {job['id']} (project: {job['project']}) on "
        f"{os.uname().nodename}, driven by an orchestrator you cannot talk to directly.\n"
        f"Job goal: {job['description']}\n"
        f"Context files from the orchestrator (read what is relevant): {directory / 'context'}\n"
        f"Put any files the orchestrator should collect in: {directory / 'outbox'}\n"
        "Finish each step with a short plain summary of what you did and anything left open, then a final line "
        "`FLEET_STATUS: done`, `FLEET_STATUS: blocked — <reason>` (you could not do the work, e.g. tools or "
        "access failed) or `FLEET_STATUS: failed — <reason>` (you tried and it did not work). Never report done "
        "for work you could not actually carry out.\n\n"
    )


def _runtime_command(job: JsonObject, step: JsonObject, session_id: Optional[str]) -> List[str]:
    config = load_config()
    prompt = step["prompt"]
    if step["index"] == 0:
        prompt = job_preamble(job) + prompt
    if job["agent"] == "claude":
        command = [config.get("claude", "claude"), "-p", prompt, "--output-format", "stream-json",
                   "--verbose", "--permission-mode", job["permission"]]
        if job.get("model"):
            command += ["--model", job["model"]]
        if job.get("allowed_tools"):
            command += ["--allowedTools", *job["allowed_tools"]]
        if session_id:
            command += ["--resume", session_id]
        command += ["--add-dir", str(JOBS_DIRECTORY / job["id"])]
        command += ["--settings", json.dumps(input_hook_settings(job["project"], job["id"], step["index"]))]
        for directory in job.get("add_dirs", []):
            command += ["--add-dir", directory]
        return command
    codex = config.get("codex", "codex")
    sandbox_flags = {"read-only": ["--sandbox", "read-only"],
                     "workspace-write": ["--full-auto"],
                     "danger-full-access": ["--dangerously-bypass-approvals-and-sandbox"]}[job["permission"]]
    model_flags = ["--model", job["model"]] if job.get("model") else []
    if session_id:
        # `exec resume` has no --sandbox flag, so the job's sandbox is passed as a config override.
        return [codex, "exec", "resume", "--json", "--skip-git-repo-check",
                "-c", f'sandbox_mode="{job["permission"]}"', *model_flags, session_id, prompt]
    return [codex, "exec", "--json", "--skip-git-repo-check", *sandbox_flags, *model_flags,
            "-C", job["cwd"], "--add-dir", str(JOBS_DIRECTORY / job["id"]), prompt]


class _Runtime:
    """Worker-local runtime differences, including resumed answer delivery."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.parser = {"claude": ClaudeParser, "codex": CodexParser}[name]()

    def command(self, job: JsonObject, step: JsonObject, session_id: Optional[str]) -> List[str]:
        return _runtime_command(job, step, session_id)

    def parse(self, record: JsonObject) -> Tuple[List[JsonObject], Optional[JsonObject]]:
        events = self.parser.parse(record)
        result = None
        if self.name == "codex" and record.get("type") in ("turn.completed", "turn.failed"):
            result = {"ok": record["type"] == "turn.completed"}
        for event in events:
            if event["kind"] == "result":
                result = {"ok": event["ok"], "summary": event["summary"], "text": event.pop("text", "")}
        if result is not None:
            tokens = record.get("usage")
            cost = record.get("total_cost_usd")
            result["usage"] = None if tokens is None and cost is None else {"tokens": tokens, "cost_usd": cost}
            for event in events:
                if event["kind"] == "result":
                    event["usage"] = result["usage"]
        return events, result

    def finish(self, outcome: JsonObject, exit_code: int, last_text: str) -> None:
        if self.name == "codex":
            outcome.update(ok=exit_code == 0,
                           summary=shorten(last_text, 400), text=last_text)
        if not outcome["summary"] and exit_code != 0:
            outcome["summary"] = f"agent exited with code {exit_code}"

    def usage(self, steps: List[JsonObject]) -> Optional[JsonObject]:
        reports = [step.get("usage") for step in steps]
        if not any(report is not None for report in reports):
            return None
        source = {"claude": "claude.result", "codex": "codex.turn.completed"}[self.name]
        return {"source": source, "reports": reports}

    def validate_permission(self, permission: str) -> None:
        if self.name == "codex" and permission not in ("read-only", "workspace-write", "danger-full-access"):
            fail("codex permission must be read-only, workspace-write or danger-full-access")

    def dispatch_permission(self, permission: Optional[str], allow: List[str], add_dirs: List[str]) -> str:
        if self.name != "claude":
            if allow:
                raise ValueError("--allow applies to claude jobs only (codex uses its sandbox)")
            if add_dirs:
                raise ValueError("--add-dir applies to claude jobs only")
        if permission is not None:
            return permission
        return {"claude": "acceptEdits", "codex": "workspace-write"}[self.name]

    def transcript_parser(self):
        return {"claude": ClaudeParser, "codex": CodexRolloutParser}[self.name]()

    def transcript_id(self, path: Path) -> str:
        return path.stem if self.name == "claude" else path.stem[-36:]

    def consume_transcript(self, transcript, record: JsonObject, head: bool) -> None:
        {"claude": transcript._claude_record, "codex": transcript._codex_record}[self.name](record, head)

    def resume_command(self) -> str:
        return {"claude": "claude --resume", "codex": "codex resume"}[self.name]


def _runtime(name: str) -> _Runtime:
    return _Runtime(name)


def agent_command(job: JsonObject, step: JsonObject, session_id: Optional[str]) -> List[str]:
    return _runtime(job["agent"]).command(job, step, session_id)


STATUS_LINE = re.compile(r"FLEET_STATUS:\s*\**\s*(done|blocked|failed)\b", re.IGNORECASE)


def reported_status(text: str) -> Optional[str]:
    """The agent's own verdict from its last FLEET_STATUS line; None when it gave none."""
    matches = STATUS_LINE.findall(text or "")
    return matches[-1].lower() if matches else None


def record_written_documents(job_id: str, cwd: str, paths: List[str], step_index: int) -> None:
    """Remember Markdown files the agent wrote so the orchestrator and the deck can find them."""
    with locked_job(job_id) as live_job:
        written = live_job.setdefault("written_documents", [])
        known = {entry["path"] for entry in written}
        for path in paths:
            absolute = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
            if absolute not in known:
                written.append({"path": absolute, "step": step_index})
                known.add(absolute)


def copy_written_documents(job_id: str, cwd: str) -> None:
    """Keep agent-written Markdown under the job's approved document root."""
    with locked_job(job_id) as live_job:
        artifacts = JOBS_DIRECTORY / job_id / "artifacts"
        for index, entry in enumerate(live_job.get("written_documents", [])):
            source = Path(entry["path"])
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(Path(cwd).resolve()):
                continue
            artifacts.mkdir(exist_ok=True)
            target = artifacts / f"file-{index}{source.suffix}"
            temporary = artifacts / f"file-{index}.tmp"
            shutil.copy2(source, temporary)
            temporary.replace(target)
            entry["artifact"] = str(target)


def run_step(job: JsonObject, step: JsonObject) -> JsonObject:
    job_id = job["id"]
    config = load_config()
    environment = dict(os.environ)
    if config.get("path"):
        environment["PATH"] = config["path"]
    if config.get("ssh_auth_sock"):
        environment["SSH_AUTH_SOCK"] = config["ssh_auth_sock"]
    environment.update(job.get("env", {}))
    environment["FLEET_JOB_ID"] = job_id
    environment["FLEET_JOB_DIR"] = str(JOBS_DIRECTORY / job_id)
    runtime = _runtime(job["agent"])
    command = agent_command(job, step, job.get("session_id"))
    append_event(job_id, {"kind": "step", "step": step["index"], "status": "running", "summary": step["title"]})
    outcome: JsonObject = {"ok": False, "summary": "", "text": "", "usage": None}
    result_recorded = False
    last_text = ""
    raw_path = JOBS_DIRECTORY / job_id / f"raw-{step['index']}.jsonl"
    with open(raw_path, "a") as raw_file:
        process = subprocess.Popen(command, cwd=job["cwd"], env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        with locked_job(job_id) as live_job:
            live_job["agent_pid"] = process.pid
        assert process.stdout is not None
        for line in process.stdout:
            raw_file.write(line)
            raw_file.flush()
            try:
                record = json.loads(line)
            except ValueError:
                if line.strip():
                    append_event(job_id, {"kind": "log", "step": step["index"], "summary": shorten(line)})
                continue
            events, result = runtime.parse(record)
            if result is not None:
                result_recorded = True
                outcome.update(result)
            for event in events:
                event["step"] = step["index"]
                if event["kind"] == "session" and event.get("session_id"):
                    with locked_job(job_id) as live_job:
                        live_job["session_id"] = event["session_id"]
                    job["session_id"] = event["session_id"]
                if event["kind"] == "todos":
                    with locked_job(job_id) as live_job:
                        live_job["todos"] = event["todos"]
                if event.get("paths"):
                    record_written_documents(job_id, job["cwd"], event["paths"], step["index"])
                if event["kind"] == "text":
                    last_text = event.pop("text", None) or event["summary"]
                append_event(job_id, event)
        exit_code = process.wait()
    runtime.finish(outcome, exit_code, last_text)
    outcome["exit_code"] = exit_code
    if exit_code < 0 and not result_recorded:
        outcome["reason"] = "lost"
    reported = reported_status(outcome.get("text") or outcome["summary"])
    if reported is not None:
        outcome["reported_status"] = reported
        if reported != "done":
            outcome["ok"] = False
    copy_written_documents(job_id, job["cwd"])
    return outcome


def run_job(job_id: str) -> None:
    """Runner loop: executes pending steps in order until none remain."""
    with locked_job(job_id) as job:
        if runner_alive(job) and job.get("runner_pid") != os.getpid():
            return
        job["runner_pid"] = os.getpid()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        while True:
            with locked_job(job_id) as job:
                if job.get("cancelled"):
                    break
                step = next((candidate for candidate in job["steps"] if candidate["status"] == "pending"), None)
                if step is None:
                    break
                step["status"] = "running"
                step["started_at"] = now()
                job["todos"] = []
            outcome = run_step(job, step)
            with locked_job(job_id) as job:
                live_step = job["steps"][step["index"]]
                if job.get("cancelled"):
                    live_step["status"] = "cancelled"
                else:
                    live_step["status"] = "done" if outcome["ok"] else "failed"
                live_step["finished_at"] = now()
                live_step["result"] = outcome["summary"]
                live_step["usage"] = outcome.get("usage")
                if "reason" in outcome:
                    live_step["reason"] = outcome["reason"]
                (JOBS_DIRECTORY / job_id / f"result-{step['index']}.md").write_text(outcome.get("text") or outcome["summary"])
                job["agent_pid"] = None
                stop = live_step["status"] != "done" and job.get("stop_on_failure", True)
            append_event(job_id, {"kind": "step", "step": step["index"], "status": live_step["status"],
                                  "summary": outcome["summary"]})
            if stop:
                break
    finally:
        with locked_job(job_id) as job:
            job["runner_pid"] = None
            status = derive_status(job)
        append_event(job_id, {"kind": "job", "status": status, "summary": f"job {status}"})


def launch_runner(job_id: str) -> None:
    job = read_job(job_id)
    if runner_alive(job):
        return
    session = tmux_session(job_id)
    subprocess.run([*TMUX_COMMAND, "kill-session", "-t", session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    environment = [f"FLEET_HOME={FLEET_HOME}", f"PATH={os.environ['PATH']}"]
    environment += [f"{key}={os.environ[key]}" for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")
                    if key in os.environ]
    runner_command = shlex.join(["env", *environment, sys.executable, os.path.abspath(__file__), "_run", job_id])
    log_path = JOBS_DIRECTORY / job_id / "runner.log"
    subprocess.run([*TMUX_COMMAND, "new-session", "-d", "-s", session, "-c", job["cwd"],
                    f"{runner_command} 2>&1 | tee -a {shlex.quote(str(log_path))}"], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        job = read_job(job_id)
        if runner_alive(job) or derive_status(job) in TERMINAL_STATUSES:
            return
        session_state = subprocess.run([*TMUX_COMMAND, "has-session", "-t", session],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if session_state.returncode != 0:
            # The launcher still knows the job even when the runner cannot load it.
            reason = "runner exited before starting the job"
            for line in tail_lines(log_path, 40):
                with contextlib.suppress(ValueError):
                    record = json.loads(line)
                    if isinstance(record, dict) and "error" in record:
                        reason = record["error"]
            with locked_job(job_id) as job:
                if derive_status(job) != "queued":
                    return
                step = next(step for step in job["steps"] if step["status"] == "pending")
                step.update(status="failed", finished_at=now(), result=reason, reason=reason)
            append_event(job_id, {"kind": "job", "status": "failed", "summary": reason})
            return
        time.sleep(0.1)


# ------------------------------------------------------------------ views


def job_summary(job: JsonObject, event_count: int) -> JsonObject:
    status = derive_status(job)
    events = read_events(job["id"], max(event_count, 1))
    activity = next((event for event in reversed(events) if event.get("kind") in ("tool", "text", "error")), None)
    return {
        "id": job["id"], "host": os.uname().nodename, "project": job["project"],
        "schema_version": DISPATCH_SCHEMA_VERSION, "run_id": job.get("run_id"),
        "usage_schema_version": USAGE_SCHEMA_VERSION, "usage": _runtime(job["agent"]).usage(job["steps"]),
        "fingerprint": job.get("fingerprint"), "start_requested": job.get("start_requested"),
        "description": job["description"], "agent": job["agent"], "model": job.get("model"),
        "cwd": job["cwd"], "permission": job["permission"], "status": status,
        "created_at": job["created_at"], "updated_at": job.get("updated_at"),
        "steps": [{key: step.get(key) for key in ("index", "title", "status", "started_at", "finished_at", "result")}
                  for step in job["steps"]],
        "todos": job.get("todos", []),
        "activity": activity,
        "events": events[-event_count:] if event_count else [],
        "session_id": job.get("session_id"),
        "tmux": shlex.join([*TMUX_COMMAND[:3], "attach", "-t", tmux_session(job['id'])]),
        "documents": job_documents(job),
        "trace": {"path": str(JOBS_DIRECTORY / job["id"] / "events.jsonl"),
                  "availability": "available" if (JOBS_DIRECTORY / job["id"] / "events.jsonl").is_file()
                  else "unavailable"},
    }


def job_documents(job: JsonObject) -> List[JsonObject]:
    """Markdown the job produced: step reports, files the agent wrote, and outbox files.

    Only these can be read back with `fleetd read`, so the deck can never be used
    to fetch arbitrary files from the host.
    """
    directory = JOBS_DIRECTORY / job["id"]
    documents: List[JsonObject] = []

    def describe(document_id: str, path: Path, kind: str, name: str, step: Optional[int],
                 display_path: Optional[str] = None) -> None:
        with contextlib.suppress(OSError):
            stat = path.stat()
            if stat.st_size and path.is_file():
                document = {"id": document_id, "kind": kind, "name": name, "step": step,
                            "path": display_path or str(path), "size": stat.st_size,
                            "mtime": round(stat.st_mtime, 3)}
                if display_path is not None:
                    document["read_path"] = str(path)
                documents.append(document)

    for step in job["steps"]:
        report = directory / f"result-{step['index']}.md"
        with contextlib.suppress(OSError):
            if report.stat().st_size >= REPORT_MINIMUM_BYTES:
                describe(f"report-{step['index']}", report, "report", f"Step {step['index'] + 1}: {step['title']}",
                         step["index"])
    for index, entry in enumerate(job.get("written_documents", [])):
        describe(f"file-{index}", Path(entry.get("artifact", entry["path"])), "file",
                 os.path.basename(entry["path"]), entry.get("step"),
                 entry["path"] if "artifact" in entry else None)
    outbox = directory / "outbox"
    if outbox.exists():
        for path in sorted(outbox.rglob("*")):
            if is_markdown(path.name):
                describe(f"outbox-{path.relative_to(outbox)}", path, "outbox", str(path.relative_to(outbox)), None)
    return documents


def command_read(arguments: argparse.Namespace) -> None:
    job = read_job(arguments.job)
    document = next((item for item in job_documents(job) if item["id"] == arguments.document), None)
    if document is None:
        fail(f"job {arguments.job} has no document {arguments.document}")
    path = Path(document.pop("read_path", document["path"])).resolve()
    roots = [JOBS_DIRECTORY / job["id"], *(Path(root).expanduser() for root in load_config().get("document_roots", []))]
    if not any(path.is_relative_to(root.resolve()) for root in roots):
        fail(f"document path outside approved document roots: {document['path']}")
    with open(path, "rb") as handle:
        raw = handle.read(DOCUMENT_READ_LIMIT + 1)
    document["truncated"] = len(raw) > DOCUMENT_READ_LIMIT
    document["content"] = raw[:DOCUMENT_READ_LIMIT].decode(errors="replace")
    document.update({"job": job["id"], "project": job["project"], "agent": job["agent"],
                     "host": os.uname().nodename, "job_description": job["description"]})
    emit(document)


def all_jobs() -> List[JsonObject]:
    jobs = []
    if JOBS_DIRECTORY.exists():
        for path in JOBS_DIRECTORY.glob("*/job.json"):
            with contextlib.suppress(ValueError, OSError):
                jobs.append(json.loads(path.read_text()))
    return sorted(jobs, key=lambda job: job["created_at"])


# ------------------------------------------------------ interactive sessions


# Interactive CLI sessions are found through their transcripts. A session is shown while its
# transcript changed in the last SESSION_ACTIVE_SECONDS: `working` while it is still being written,
# `idle` (waiting on the human) once it has been quiet for SESSION_WORKING_SECONDS.
SESSION_ACTIVE_SECONDS = 20 * 60
SESSION_WORKING_SECONDS = 90
SESSION_SCAN_INTERVAL = 2.0
SESSION_EVENTS = 15
SESSION_TITLE_LENGTH = 80
SESSION_HEAD_BYTES = 256 * 1024  # read once per transcript for its start time and first prompt
SESSION_TAIL_BYTES = 1024 * 1024  # most read on first sight, or when a transcript grew by more
FLEET_PREAMBLE = "You are running as fleet job "
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
COMMAND_ARGUMENTS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def parse_timestamp(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return round(datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp(), 3)
    except ValueError:
        return None


repository_names: Dict[str, str] = {}


def repository_name(cwd: str) -> str:
    """The git repository a directory belongs to (the main repository for a worktree), else its basename."""
    if cwd not in repository_names:
        repository_names[cwd] = find_repository_name(Path(cwd)) or os.path.basename(cwd.rstrip("/")) or cwd
    return repository_names[cwd]


def find_repository_name(path: Path) -> Optional[str]:
    for directory in (path, *path.parents):
        marker = directory / ".git"
        try:
            if marker.is_dir():
                return directory.name
            if not marker.is_file():
                continue
            pointer = marker.read_text().strip()
        except OSError:
            continue
        if not pointer.startswith("gitdir:"):
            return directory.name
        # A worktree's .git file points into <main>/.git/worktrees/<name>; `commondir` leads back to <main>/.git.
        git_directory = (directory / pointer[len("gitdir:"):].strip()).resolve()
        common = git_directory
        with contextlib.suppress(OSError):
            common = (git_directory / (git_directory / "commondir").read_text().strip()).resolve()
        name = common.parent.name if common.name == ".git" else common.name
        return name[:-len(".git")] if name.endswith(".git") else name
    return None


def claude_prompt(content: Any) -> str:
    """The human's text in a Claude transcript user record; empty for tool results and harness messages."""
    if isinstance(content, list):
        if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return ""
        content = " ".join(block.get("text", "") for block in content
                           if isinstance(block, dict) and block.get("type") == "text")
    text = str(content or "").strip()
    if not text.startswith("<"):
        return text
    name, arguments = COMMAND_NAME.search(text), COMMAND_ARGUMENTS.search(text)
    if name and arguments and arguments.group(1).strip():
        return f"{name.group(1).strip()} {arguments.group(1).strip()}"
    return ""


def codex_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return " ".join(block.get("text", "") for block in content or [] if isinstance(block, dict))


class CodexRolloutParser:
    """Turns an interactive Codex CLI rollout (~/.codex/sessions/…/rollout-*.jsonl) into fleet events.

    Rollouts differ from `codex exec --json`: activity arrives as `event_msg` records with an
    `item_completed` payload of typed items, and plans as `update_plan` function calls.
    """

    def parse(self, record: JsonObject) -> List[JsonObject]:
        payload = record.get("payload") or {}
        record_type, payload_type = record.get("type"), payload.get("type")
        if record_type == "response_item" and payload_type == "function_call" and payload.get("name") == "update_plan":
            return self._plan(payload.get("arguments"))
        if record_type != "event_msg":
            return []
        if payload_type == "error":
            return [{"kind": "error", "summary": shorten(payload.get("message"))}]
        if payload_type != "item_completed":
            return []
        item = payload.get("item") or {}
        item_type = item.get("type")
        if item_type == "AgentMessage":
            text = codex_text(item.get("content"))
            return [{"kind": "text", "summary": shorten(text)}] if text.strip() else []
        if item_type == "Reasoning":
            summary = " ".join(codex_text([part]) if isinstance(part, dict) else str(part)
                               for part in item.get("summary_text") or [])
            return [{"kind": "tool", "tool": "think", "summary": shorten(summary) or "thinking…"}]
        if item_type == "CommandExecution":
            command = item.get("command")
            if isinstance(command, list):
                command = command[-1] if command else ""
            events = [{"kind": "tool", "tool": "bash", "name": "shell", "summary": shorten(command)}]
            if item.get("status") == "failed":
                events.append({"kind": "error", "summary": shorten(f"exit {item.get('exit_code')}: {command}")})
            return events
        if item_type == "FileChange":
            changes = item.get("changes") or {}
            paths = [path for path, change in changes.items()
                     if not isinstance(change, dict) or change.get("type") != "delete"] if isinstance(changes, dict) else []
            return [{"kind": "tool", "tool": "edit", "name": "apply_patch", "summary": shorten(", ".join(paths))}]
        if item_type == "Extension" and item.get("kind") == "web.search":
            return [{"kind": "tool", "tool": "web", "name": "web_search", "summary": shorten(item.get("query"))}]
        if item_type == "ImageView":
            return [{"kind": "tool", "tool": "read", "name": "view_image", "summary": shorten(item.get("path"))}]
        if item_type == "SubAgentActivity" and item.get("kind") == "started":
            return [{"kind": "tool", "tool": "delegate", "name": "spawn_agent", "summary": shorten(item.get("agent_path"))}]
        return []

    def _plan(self, arguments: Any) -> List[JsonObject]:
        try:
            plan = json.loads(arguments).get("plan") or []
        except (TypeError, ValueError, AttributeError):
            return []
        return [{"kind": "todos", "todos": [{"text": step.get("step", ""), "status": step.get("status", "pending")}
                                            for step in plan if isinstance(step, dict)]}]


def codex_prompt(record: JsonObject) -> str:
    payload = record.get("payload") or {}
    if record.get("type") != "event_msg":
        return ""
    if payload.get("type") == "user_message":
        text = str(payload.get("message") or "")
    elif payload.get("type") == "item_completed" and (payload.get("item") or {}).get("type") == "UserMessage":
        text = codex_text(payload["item"].get("content"))
    else:
        return ""
    text = text.strip()
    return "" if text.startswith(("<", "# AGENTS.md")) else text


class Transcript:
    """What is known about one interactive session, read incrementally from its transcript."""

    def __init__(self, path: Path, agent: str) -> None:
        self.path = path
        self.agent = agent
        self.runtime = _runtime(agent)
        self.signature: Optional[tuple] = None
        self.offset = 0
        self.parser = self.runtime.transcript_parser()
        # Codex names rollouts rollout-<local time>-<thread id>.jsonl; session_meta confirms the id.
        self.id = self.runtime.transcript_id(path)
        self.cwd: Optional[str] = None
        self.model: Optional[str] = None
        self.started_at: Optional[float] = None
        self.titles: Dict[str, str] = {}
        self.hidden = False  # a sub-agent's transcript or a fleet job's own session
        self.events: Deque[JsonObject] = collections.deque(maxlen=SESSION_EVENTS)
        self.activity: Optional[JsonObject] = None
        self.todos: List[JsonObject] = []

    def refresh(self, size: int) -> None:
        if size < self.offset:  # rewritten from scratch
            self.__init__(self.path, self.agent)  # type: ignore[misc]
        if self.offset == 0:
            self._read_head()
        start, partial_first_line = self.offset, False
        if size - start > SESSION_TAIL_BYTES:
            start, partial_first_line = size - SESSION_TAIL_BYTES, True
        with open(self.path, "rb") as handle:
            handle.seek(start)
            data = handle.read(size - start)
        complete = data.rfind(b"\n") + 1  # a line still being written is read next time
        self.offset = start + complete
        lines = data[:complete].splitlines()
        for line in lines[1:] if partial_first_line else lines:
            self._consume(line, head=False)

    def _read_head(self) -> None:
        consumed = 0
        with open(self.path, "rb") as handle:
            for line in handle:
                self._consume(line, head=True)
                consumed += len(line)
                if consumed > SESSION_HEAD_BYTES or ("prompt" in self.titles and self.started_at is not None):
                    return

    def _consume(self, line: bytes, head: bool) -> None:
        try:
            record = json.loads(line)
        except ValueError:
            return
        if not isinstance(record, dict):
            return
        self.runtime.consume_transcript(self, record, head)

    def _claude_record(self, record: JsonObject, head: bool) -> None:
        if record.get("isSidechain"):
            return
        record_type = record.get("type")
        for title_type, key, field in (("custom-title", "custom", "customTitle"), ("ai-title", "ai", "aiTitle"),
                                       ("summary", "summary", "summary")):
            if record_type == title_type and record.get(field):
                self.titles[key] = str(record[field])
        if self.started_at is None and record.get("timestamp"):
            self.started_at = parse_timestamp(record["timestamp"])
        if record.get("cwd"):
            self.cwd = record["cwd"]
        message = record.get("message") or {}
        model = message.get("model")
        if record_type == "assistant" and model and not str(model).startswith("<"):  # skip "<synthetic>"
            self.model = model
        if record_type == "user" and not record.get("isMeta") and "prompt" not in self.titles:
            self._set_prompt(claude_prompt(message.get("content")))
        if head:
            return
        if "toolUseResult" in record:  # transcripts spell the stream-json field differently
            record = {**record, "tool_use_result": record["toolUseResult"]}
        for event in self.parser.parse(record):
            self._add_event(event, record.get("timestamp"))

    def _codex_record(self, record: JsonObject, head: bool) -> None:
        payload = record.get("payload") or {}
        record_type = record.get("type")
        if record_type == "session_meta":
            self.id = payload.get("id") or self.id
            self.cwd = payload.get("cwd") or self.cwd
            self.started_at = parse_timestamp(payload.get("timestamp"))
            # Sub-agents and reviewers record their parent in a structured source; people use the cli.
            self.hidden = self.hidden or not isinstance(payload.get("source"), str)
        elif record_type == "turn_context":
            self.model = payload.get("model") or self.model
            self.cwd = payload.get("cwd") or self.cwd
        elif "prompt" not in self.titles:
            self._set_prompt(codex_prompt(record))
        if not head:
            for event in self.parser.parse(record):
                self._add_event(event, record.get("timestamp"))

    def _set_prompt(self, prompt: str) -> None:
        if prompt:
            self.titles["prompt"] = shorten(prompt, SESSION_TITLE_LENGTH)
            self.hidden = self.hidden or prompt.startswith(FLEET_PREAMBLE)

    def _add_event(self, event: JsonObject, timestamp: Any) -> None:
        if event["kind"] in ("session", "result"):
            return
        event.pop("text", None)
        event.pop("paths", None)
        event["ts"] = parse_timestamp(timestamp) or now()
        if event["kind"] == "todos":
            self.todos = event["todos"]
        elif event["kind"] in ("tool", "text", "error"):
            self.activity = event
        self.events.append(event)

    def summary(self, status: str, updated_at: float) -> JsonObject:
        title = next((self.titles[key] for key in ("custom", "ai", "summary", "prompt") if self.titles.get(key)), None)
        resume = self.runtime.resume_command()
        return {
            "id": self.id, "host": os.uname().nodename, "agent": self.agent, "cwd": self.cwd,
            "project": repository_name(self.cwd) if self.cwd else None,
            "title": shorten(title, SESSION_TITLE_LENGTH) if title else None,
            "status": status, "started_at": self.started_at, "updated_at": updated_at, "model": self.model,
            "todos": self.todos, "activity": self.activity, "events": list(self.events),
            "resume": f"cd {shlex.quote(self.cwd or '.')} && {resume} {self.id}",
        }


def scan_directory(path: Path) -> List[os.DirEntry]:
    try:
        with os.scandir(path) as entries:
            return list(entries)
    except OSError:
        return []


class SessionTracker:
    """Finds live interactive Claude Code and Codex CLI sessions on this machine.

    Each scan only stats transcripts; one that changed is read from where the previous
    read stopped. Fleet jobs' own sessions are left out — the deck shows those as jobs.
    """

    def __init__(self) -> None:
        self.transcripts: Dict[Path, Transcript] = {}
        self.job_sessions: Dict[Path, Tuple[int, Optional[str]]] = {}

    def candidates(self) -> Iterator[Tuple[Path, str, os.stat_result]]:
        horizon = time.time() - SESSION_ACTIVE_SECONDS
        # Only top-level transcripts: sub-agents write theirs in a subdirectory of the session.
        for project in scan_directory(CLAUDE_PROJECTS_DIRECTORY):
            for entry in scan_directory(Path(project.path)) if project.is_dir() else []:
                if entry.name.endswith(".jsonl"):
                    with contextlib.suppress(OSError):
                        stat = entry.stat()
                        if stat.st_mtime >= horizon:
                            yield Path(entry.path), "claude", stat
        today = datetime.date.today()
        for day in (today, today - datetime.timedelta(days=1)):
            for entry in scan_directory(CODEX_SESSIONS_DIRECTORY / day.strftime("%Y/%m/%d")):
                if entry.name.startswith("rollout-") and entry.name.endswith(".jsonl"):
                    with contextlib.suppress(OSError):
                        stat = entry.stat()
                        if stat.st_mtime >= horizon:
                            yield Path(entry.path), "codex", stat

    def fleet_session_ids(self) -> set:
        """Session ids recorded by fleet jobs; job.json is re-read only when it changes."""
        known: Dict[Path, Tuple[int, Optional[str]]] = {}
        for path in JOBS_DIRECTORY.glob("*/job.json") if JOBS_DIRECTORY.exists() else []:
            try:
                modified = path.stat().st_mtime_ns
            except OSError:
                continue
            cached = self.job_sessions.get(path)
            if cached is None or cached[0] != modified:
                session_id = None
                with contextlib.suppress(ValueError, OSError):
                    session_id = json.loads(path.read_text()).get("session_id")
                cached = (modified, session_id)
            known[path] = cached
        self.job_sessions = known
        return {session_id for _, session_id in known.values() if session_id}

    def scan(self) -> Dict[str, JsonObject]:
        """Current sessions by id."""
        job_sessions = self.fleet_session_ids()
        sessions: Dict[str, JsonObject] = {}
        live: Dict[Path, Transcript] = {}
        clock = time.time()
        for path, agent, stat in self.candidates():
            transcript = live[path] = self.transcripts.get(path) or Transcript(path, agent)
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature != transcript.signature:
                transcript.signature = signature
                try:
                    transcript.refresh(stat.st_size)
                except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError):
                    continue  # an unexpected record shape must not take the stream down
            if transcript.hidden or transcript.cwd is None or transcript.id in job_sessions:
                continue
            status = "working" if clock - stat.st_mtime < SESSION_WORKING_SECONDS else "idle"
            sessions[transcript.id] = transcript.summary(status, round(stat.st_mtime, 3))
        self.transcripts = live
        return sessions


# --------------------------------------------------------------- pipelines

PIPELINES_DIRECTORY = FLEET_HOME / "pipelines"
PIPELINE_SCAN_INTERVAL = 1.0
PIPELINE_EMIT_INTERVAL = 1.0
PIPELINE_RUNS = 2            # the newest run and the one before it, its baseline if it finished
PIPELINE_RECENT = 25         # items kept per node, for the terminal nodes' drill-down
PIPELINE_RATE_WINDOW = 10    # seconds of flows behind the items-per-second figure
PIPELINE_READ_BYTES = 16 * 1024 * 1024  # most read per scan, so catching up on a long run never stalls the stream


class PipelineRun:
    """An aggregate of one run's event file (`pipelines/<pipeline>/<run id>.jsonl`), read incrementally.

    Lines: {"type": "run", nodes, label, total, tones?, ends?, …} once (tones: node → good, warn or muted, how the deck
    colours the bands into it; ends: the nodes before the last column that items may stop at, so that while the run
    goes the deck shows what the others hold as waiting), {"type": "flow", item, from, to, ts, attrs?} per item
    and edge, {"type": "end", status, ts} at the end. Raw events never leave the host; only the summary does.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.caught_up = False
        self.modified: Optional[float] = None
        self.meta: JsonObject = {}
        self.edges: Dict[Tuple[str, str], int] = {}
        self.inflow: Dict[str, int] = collections.Counter()
        self.outflow: Dict[str, int] = collections.Counter()
        self.recent: Dict[str, Deque[JsonObject]] = {}
        self.per_second: Dict[str, Dict[int, int]] = {}   # flows out of each node, by the second of their ts
        self.status: Optional[str] = None
        self.ended_at: Optional[float] = None

    def refresh(self, size: int, modified: float) -> None:
        if size < self.offset:  # rewritten from scratch
            self.__init__(self.path)  # type: ignore[misc]
        self.modified = modified
        start = self.offset
        with open(self.path, "rb") as handle:
            handle.seek(start)
            data = handle.read(min(size - start, PIPELINE_READ_BYTES))
        complete = data.rfind(b"\n") + 1  # a line still being written is read next time
        self.offset = start + complete
        self.caught_up = start + len(data) >= size
        for line in data[:complete].splitlines():
            self._consume(line)

    def _consume(self, line: bytes) -> None:
        try:
            record = json.loads(line)
        except ValueError:
            return
        if not isinstance(record, dict):
            return
        kind = record.get("type")
        if kind == "run":
            self.meta = record
        elif kind == "end":
            self.status = str(record.get("status") or "done")
            self.ended_at = record.get("ts")
        elif kind == "flow" and record.get("from") is not None and record.get("to") is not None:
            source, target = str(record["from"]), str(record["to"])
            self.edges[(source, target)] = self.edges.get((source, target), 0) + 1
            self.outflow[source] += 1
            self.inflow[target] += 1
            ts = record.get("ts") if isinstance(record.get("ts"), (int, float)) else now()
            seconds = self.per_second.setdefault(source, collections.Counter())
            seconds[int(ts)] += 1
            item: JsonObject = {"item": record.get("item"), "ts": ts}
            if isinstance(record.get("attrs"), dict):
                item["attrs"] = record["attrs"]
            self.recent.setdefault(target, collections.deque(maxlen=PIPELINE_RECENT)).append(item)

    def columns(self) -> List[List[str]]:
        """The run line's columns, with any node it did not list in the column after its source's."""
        columns = [[str(node) for node in column] for column in self.meta.get("nodes") or [] if isinstance(column, list)]
        placed = {node: index for index, column in enumerate(columns) for node in column}

        def place(node: str, index: int) -> None:
            while len(columns) <= index:
                columns.append([])
            columns[index].append(node)
            placed[node] = index

        for source in self.outflow:
            if source not in placed and not self.inflow[source]:
                place(source, 0)  # an unlisted source starts at the left
        changed = True
        while changed:  # nodes only reachable through a cycle of unlisted nodes stay out
            changed = False
            for source, target in self.edges:
                if target not in placed and source in placed:
                    place(target, placed[source] + 1)
                    changed = True
        return columns

    def counts(self) -> Dict[str, int]:
        return {node: max(self.inflow[node], self.outflow[node]) for node in set(self.inflow) | set(self.outflow)}

    def summary(self, clock: float) -> JsonObject:
        horizon = int(clock) - PIPELINE_RATE_WINDOW
        for seconds in self.per_second.values():
            for second in [second for second in seconds if second <= horizon]:
                del seconds[second]
        columns = self.columns()
        entered = sum(sum(self.per_second.get(node, {}).values()) for node in (columns[0] if columns else []))
        return {
            "run_id": self.meta.get("run_id") or self.path.stem, "pipeline": self.meta.get("pipeline"),
            "label": self.meta.get("label"), "started_at": self.meta.get("started_at"), "total": self.meta.get("total"),
            "tones": self.meta["tones"] if isinstance(self.meta.get("tones"), dict) else None,
            "ends": [str(node) for node in self.meta["ends"]] if isinstance(self.meta.get("ends"), list) else None,
            "nodes": columns, "edges": [[source, target, count] for (source, target), count in self.edges.items()],
            "counts": self.counts(), "flows": sum(self.edges.values()),
            "recent": {node: list(items) for node, items in self.recent.items() if not self.outflow[node]},
            "item_rate": round(entered / PIPELINE_RATE_WINDOW, 1), "updated_at": self.modified,
            "status": self.status or "running", "ended_at": self.ended_at,
        }

    def baseline(self) -> JsonObject:
        return {"run_id": self.meta.get("run_id") or self.path.stem, "label": self.meta.get("label"),
                "started_at": self.meta.get("started_at"), "total": self.meta.get("total"),
                "edges": [[source, target, count] for (source, target), count in self.edges.items()],
                "counts": self.counts()}


class PipelineTracker:
    """Follows the newest runs of every pipeline under FLEET_HOME/pipelines and says what changed.

    A pipeline is announced once its runs have been read to the end, then at most once
    every PIPELINE_EMIT_INTERVAL seconds while its summary keeps changing.
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = directory or PIPELINES_DIRECTORY
        self.runs: Dict[Path, PipelineRun] = {}
        self.sent: Dict[str, JsonObject] = {}
        self.sent_at: Dict[str, float] = {}

    def scan(self, clock: Optional[float] = None) -> List[JsonObject]:
        clock = time.time() if clock is None else clock
        messages: List[JsonObject] = []
        live: Dict[Path, PipelineRun] = {}
        for folder in sorted(scan_directory(self.directory), key=lambda entry: entry.name):
            if not folder.is_dir():
                continue
            names = sorted(entry.name for entry in scan_directory(Path(folder.path)) if entry.name.endswith(".jsonl"))
            runs = []
            for name in names[-PIPELINE_RUNS:]:
                path = Path(folder.path) / name
                run = live[path] = self.runs.get(path) or PipelineRun(path)
                try:
                    stat = path.stat()
                    modified = round(stat.st_mtime, 3)
                    if not run.caught_up or stat.st_size != run.offset or modified != run.modified:
                        run.refresh(stat.st_size, modified)
                except OSError:
                    continue
                runs.append(run)
            if not runs or not all(run.caught_up for run in runs):
                continue
            latest, previous = runs[-1], runs[-2] if len(runs) > 1 else None
            message = {"type": "pipeline", "pipeline": folder.name, "run": latest.summary(clock),
                       "baseline": previous.baseline() if previous and previous.status == "done" else None}
            if message != self.sent.get(folder.name) and clock - self.sent_at.get(folder.name, 0) >= PIPELINE_EMIT_INTERVAL:
                messages.append(message)
                self.sent[folder.name] = message
                self.sent_at[folder.name] = clock
        self.runs = live
        return messages


# --------------------------------------------------------------- commands


def input_hook_settings(project: str, job_id: Optional[str] = None,
                        step_index: Optional[int] = None) -> JsonObject:
    command = [sys.executable, str(Path(__file__).resolve()), "input-hook", "--project", project]
    if job_id is not None:
        command += ["--job", job_id, "--step-index", str(step_index)]
    shell_command = f"FLEET_HOME={shlex.quote(str(FLEET_HOME))} " + shlex.join(command)
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": shell_command}]}]
                      for event in ("PermissionRequest", "PostToolUse")}}


def record_input_hook(record: JsonObject, *, project: str, job_id: Optional[str] = None,
                      step_index: Optional[int] = None) -> None:
    event = record["hook_event_name"]
    if event not in ("PermissionRequest", "PostToolUse"):
        return
    session_id = record["session_id"]
    owner = [job_id, session_id, step_index]
    directory = FLEET_HOME / "input-observations"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(owner).encode()).hexdigest()
    path = directory / f"{key}.json"
    with open(directory / f"{key}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = json.loads(path.read_text()) if path.exists() else []
        match = next((item for item in reversed(records)
                      if item["kind"] == "input_requested"
                      and item["raw_request"]["tool_name"] == record["tool_name"]
                      and item["raw_request"]["tool_input"] == record["tool_input"]), None)
        if event == "PermissionRequest":
            if match is not None:
                return
            records.append({"type": "input_observation", "schema_version": 1,
                            "host": os.uname().nodename, "runtime": "claude",
                            "owner_type": "job" if job_id is not None else "session",
                            "job_id": job_id, "session_id": session_id, "step_index": step_index,
                            "project": project, "kind": "input_requested", "reason": "permission",
                            "source_event": event, "source_event_id": secrets.token_hex(16),
                            "observed_at": now(), "context_reference": str(path),
                            "raw_request": record})
        elif match is not None:
            match.update(kind="input_cleared", source_event=event, observed_at=now(), raw_resume=record)
        else:
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records))
        temporary.replace(path)


def input_observations() -> List[JsonObject]:
    observations = []
    for path in sorted((FLEET_HOME / "input-observations").glob("*.json")):
        observations.extend({**{key: value for key, value in record.items()
                                if key not in ("raw_request", "raw_resume")},
                             "request": input_request(record["raw_request"])}
                            for record in json.loads(path.read_text()))
    return observations


def input_request(raw: JsonObject) -> JsonObject:
    """What the agent asked to do, in the words a person answering needs."""
    tool_input = raw.get("tool_input") or {}
    detail = next((tool_input[key] for key in ("command", "file_path", "url", "pattern", "query", "prompt")
                   if isinstance(tool_input.get(key), str)), None)
    if detail is None:
        detail = json.dumps(tool_input) if tool_input else ""
    description = tool_input.get("description")
    return {"tool": raw.get("tool_name") or "a tool",
            "description": description if isinstance(description, str) else "",
            "detail": detail[:2000], "rules": permission_rules(raw.get("tool_name"), tool_input)}


SUBCOMMAND_PROGRAMS = {"git", "npm", "pnpm", "yarn", "npx", "uv", "cargo", "docker", "kubectl", "gh", "go",
                       "pip", "poetry", "systemctl"}
FILE_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit")
RULE = re.compile(r"[A-Za-z_][\w-]*(\(.+\))?")


def permission_rules(tool: Optional[str], tool_input: JsonObject) -> List[str]:
    """Claude permission rules that would have allowed this request; empty when none can be named."""
    if not tool:
        return []
    if tool == "Bash":
        command = tool_input.get("command")
        return bash_rules(command) if isinstance(command, str) else []
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if tool in FILE_TOOLS:
        if not isinstance(path, str) or not path:
            return []
        # `//` anchors an absolute path; a single `/` means relative to a settings file.
        return [f"{tool}(/{path})" if path.startswith("/") else f"{tool}({path})"]
    if tool == "WebFetch":
        host = urllib.parse.urlsplit(tool_input.get("url") or "").hostname
        return [f"WebFetch(domain:{host})"] if host else []
    return [tool] if RULE.fullmatch(tool) else []


def bash_rules(command: str) -> List[str]:
    """One `Bash(prefix:*)` per simple command, as Claude checks each part of a compound command."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    rules: List[str] = []
    segment: List[str] = []
    redirect = False
    for token in tokens + [";"]:
        if redirect:
            redirect = False
            continue
        if token and set(token) <= set("<>&") and set(token) & set("<>"):
            redirect = True   # the next word is the redirect's target, not a command
            continue
        if token and set(token) <= set("&|;()"):
            words = segment
            while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
                words = words[1:]   # leading VAR=value assignments
            segment = []
            if not words:
                continue
            prefix = words[:2] if words[0] in SUBCOMMAND_PROGRAMS and len(words) > 1 and not words[1].startswith("-") \
                else words[:1]
            rule = f"Bash({' '.join(prefix)}:*)"
            if rule not in rules:
                rules.append(rule)
        else:
            segment.append(token)
    return rules


def command_input_hook(arguments: argparse.Namespace) -> None:
    record_input_hook(json.load(sys.stdin), project=arguments.project,
                      job_id=arguments.job, step_index=arguments.step_index)


def make_step(index: int, prompt: str, title: Optional[str]) -> JsonObject:
    return {"index": index, "title": title or shorten(prompt.splitlines()[0] if prompt.strip() else prompt, 80),
            "prompt": prompt, "status": "pending", "started_at": None, "finished_at": None, "result": None}


def parse_steps(steps_json: str) -> List[JsonObject]:
    raw_steps = json.loads(steps_json)
    return [{"prompt": item, "title": None} if isinstance(item, str) else item for item in raw_steps]


def command_create(arguments: argparse.Namespace) -> None:
    validate_dispatch(arguments)
    job_id = arguments.id or secrets.token_hex(3)
    directory = JOBS_DIRECTORY / job_id
    cwd = os.path.abspath(os.path.expanduser(arguments.cwd))
    if not os.path.isdir(cwd):
        fail(f"working directory does not exist on {os.uname().nodename}: {cwd}")
    try:
        arguments.permission = _runtime(arguments.agent).dispatch_permission(
            arguments.permission, json.loads(arguments.allowed_tools) if arguments.allowed_tools else [],
            arguments.add_dir)
    except ValueError as error:
        fail(str(error))
    _runtime(arguments.agent).validate_permission(arguments.permission)
    steps = [make_step(index, item["prompt"], item.get("title"))
             for index, item in enumerate(parse_steps(Path(arguments.steps_file).read_text()))]
    if not steps:
        fail("a job needs at least one step")
    job = {"id": job_id, "project": arguments.project, "description": arguments.description,
           "agent": arguments.agent, "model": arguments.model, "cwd": cwd, "permission": arguments.permission,
           "stop_on_failure": not arguments.keep_going, "created_at": now(), "updated_at": now(),
           "allowed_tools": json.loads(arguments.allowed_tools) if arguments.allowed_tools else [],
           "add_dirs": [os.path.abspath(os.path.expanduser(directory)) for directory in arguments.add_dir],
           "env": dict(pair.split("=", 1) for pair in arguments.env),
           "steps": steps, "todos": [], "session_id": None, "runner_pid": None, "agent_pid": None}
    if arguments.run_id is not None:
        definition = {key: value for key, value in job.items() if key not in ("created_at", "updated_at")}
        job.update(run_id=arguments.run_id, fingerprint=arguments.fingerprint,
                   definition_fingerprint=hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest(),
                   start_requested=False)
    JOBS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with open(JOBS_DIRECTORY / ".create-lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = next((candidate for candidate in all_jobs()
                         if arguments.run_id is not None and candidate.get("run_id") == arguments.run_id), None)
        if existing is None and directory.exists():
            existing = read_job(job_id)
        if existing is not None:
            if arguments.run_id is None:
                fail(f"job already exists: {job_id}")
            if any(existing.get(key) != job[key] for key in
                   ("run_id", "fingerprint", "definition_fingerprint")):
                fail("run fingerprint has changed payload")
        else:
            (directory / "context").mkdir(parents=True)
            (directory / "outbox").mkdir()
            (directory / "job.json").write_text(json.dumps(job, indent=1))
            append_event(job_id, {"kind": "job", "status": "queued", "summary": f"job created: {arguments.description}"})
    if not arguments.hold:
        start_job(job_id, arguments)
    emit(job_summary(read_job(job_id), 0))


def validate_dispatch(arguments: argparse.Namespace) -> None:
    if arguments.run_id is None and arguments.schema_version is None and arguments.fingerprint is None:
        return
    if arguments.schema_version != DISPATCH_SCHEMA_VERSION or not arguments.run_id or not arguments.fingerprint:
        fail("dispatch requires schema version 4, run ID and fingerprint")


def start_job(job_id: str, arguments: argparse.Namespace) -> None:
    validate_dispatch(arguments)
    with locked_job(job_id) as job:
        if job.get("run_id") is not None:
            if job["run_id"] != arguments.run_id or job["fingerprint"] != arguments.fingerprint:
                fail("run fingerprint has changed payload")
            if job["start_requested"]:
                return
            job["start_requested"] = True
        elif arguments.run_id is not None:
            fail("job has no run fingerprint")
    launch_runner(job_id)


def command_reconcile(arguments: argparse.Namespace) -> None:
    if arguments.schema_version != DISPATCH_SCHEMA_VERSION:
        fail("unsupported dispatch schema version")
    job = next((job for job in all_jobs() if job.get("run_id") == arguments.run_id), None)
    if job is None:
        emit({"schema_version": DISPATCH_SCHEMA_VERSION, "run_id": arguments.run_id,
              "fingerprint": arguments.fingerprint, "status": "absent"})
        return
    if job["fingerprint"] != arguments.fingerprint:
        fail("run fingerprint has changed payload")
    summary = job_summary(job, 0)
    summary["schema_version"] = arguments.schema_version
    emit(summary)


def command_deliver(arguments: argparse.Namespace) -> None:
    if arguments.schema_version != 1:
        fail("unsupported delivery schema version")
    answer = sys.stdin.read()
    if not arguments.key.strip() or not answer.strip():
        fail("delivery key and answer are required")
    directory = job_directory(arguments.job)
    with open(directory / ".delivery-lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with locked_job(arguments.job) as job:
            step = next((step for step in job["steps"] if step.get("delivery_key") == arguments.key), None)
            if step is not None:
                if step["prompt"] != answer:
                    fail("delivery key has changed payload")
            else:
                if not job["session_id"]:
                    fail("job has no session to resume")
                if runner_alive(job):
                    emit({"schema_version": 1, "key": arguments.key, "status": "busy"})
                    return
                if job["cancelled"]:
                    fail("job is cancelled")
                step = make_step(len(job["steps"]), answer, "Answer")
                step["delivery_key"] = arguments.key
                job["steps"].append(step)
            pending = step["status"] == "pending"
        if pending:
            launch_runner(arguments.job)
        step = next(step for step in read_job(arguments.job)["steps"] if step.get("delivery_key") == arguments.key)
        if step["status"] == "pending":
            emit({"schema_version": 1, "key": arguments.key, "status": "busy"})
            return
    emit({"schema_version": 1, "key": arguments.key, "status": "applied"})


def command_grant(arguments: argparse.Namespace) -> None:
    """Add Claude permission rules to a job and queue a step that continues the refused one.

    Idempotent by key: a repeated grant reports the first result and queues nothing more.
    """
    if arguments.schema_version != 1:
        fail("unsupported grant schema version")
    rules = json.loads(sys.stdin.read() or "null")
    if (not arguments.key.strip() or not isinstance(rules, list) or not rules
            or not all(isinstance(rule, str) and RULE.fullmatch(rule) for rule in rules)):
        fail("grant key and a JSON list of permission rules are required")
    with locked_job(arguments.job) as job:
        grant = next((grant for grant in job.get("permission_grants", []) if grant["key"] == arguments.key), None)
        if grant is None:
            if job["agent"] != "claude":
                fail("permission rules apply to claude jobs only")
            if not 0 <= arguments.step < len(job["steps"]):
                fail(f"job has no step {arguments.step}")
            allowed = job.setdefault("allowed_tools", [])   # jobs created before --allowed-tools lack it
            added = [rule for rule in dict.fromkeys(rules) if rule not in allowed]
            allowed += added
            step = make_step(len(job["steps"]),
                             f"Continue step {arguments.step + 1}: the commands you were refused are now allowed "
                             f"({', '.join(rules)}). Retry what was refused, then finish that step's work.",
                             f"Continue step {arguments.step + 1}")
            job["steps"].append(step)
            job["cancelled"] = False
            grant = {"key": arguments.key, "step": arguments.step, "rules": rules, "added": added,
                     "continuation": step["index"], "at": now()}
            job.setdefault("permission_grants", []).append(grant)
            fresh = True
        else:
            fresh = False
    if fresh:
        append_event(arguments.job, {"kind": "job", "status": "queued",
                                     "summary": f"allowed {', '.join(rules)}; step {arguments.step + 1} continues"})
        launch_runner(arguments.job)
    emit({"schema_version": 1, "key": arguments.key, "status": "applied", "added": grant["added"],
          "continuation": grant["continuation"]})


def command_add(arguments: argparse.Namespace) -> None:
    new_steps = parse_steps(Path(arguments.steps_file).read_text())
    with locked_job(arguments.job) as job:
        job["cancelled"] = False
        for item in new_steps:
            job["steps"].append(make_step(len(job["steps"]), item["prompt"], item.get("title")))
        if arguments.retry:
            for step in job["steps"]:
                if step["status"] in ("failed", "cancelled"):
                    step["status"] = "pending"
    append_event(arguments.job, {"kind": "job", "status": "queued", "summary": f"{len(new_steps)} step(s) added"})
    if not arguments.hold:
        launch_runner(arguments.job)
    emit(job_summary(read_job(arguments.job), 0))


def command_start(arguments: argparse.Namespace) -> None:
    start_job(arguments.job, arguments)
    emit(job_summary(read_job(arguments.job), 0))


def command_list(arguments: argparse.Namespace) -> None:
    jobs = all_jobs()
    if not arguments.all:
        horizon = now() - arguments.since_hours * 3600
        jobs = [job for job in jobs if derive_status(job) not in TERMINAL_STATUSES or job.get("updated_at", 0) >= horizon]
    emit({"host": os.uname().nodename, "time": now(),
          "jobs": [job_summary(job, arguments.events) for job in jobs]})


def job_signature(directory: Path) -> tuple:
    """Changes whenever the job definition or its activity changes."""
    signature = []
    for name in ("job.json", "events.jsonl", "outbox"):
        with contextlib.suppress(OSError):
            stat = (directory / name).stat()
            signature += [stat.st_mtime_ns, stat.st_size]
    return tuple(signature)


def command_stream(arguments: argparse.Namespace) -> None:
    """Push job summaries as they change: hello, then job/removed lines, heartbeat every few seconds.

    Interactive sessions follow as session/session_removed lines, rescanned every
    --session-interval seconds, and pipeline runs as pipeline lines (see PipelineTracker).
    Watches file signatures rather than using inotify so it stays stdlib-only. A broken
    pipe (the ssh side went away) ends the process.
    """
    signatures: Dict[str, tuple] = {}
    runner_states: Dict[str, bool] = {}
    ignored: Dict[str, tuple] = {}  # finished jobs older than the horizon, until their files change
    horizon_seconds = arguments.since_hours * 3600
    last_heartbeat = 0.0
    tracker = SessionTracker()
    sessions: Dict[str, JsonObject] = {}
    last_session_scan = 0.0
    pipelines = PipelineTracker()
    last_pipeline_scan = 0.0
    inputs: Dict[str, JsonObject] = {}
    try:
        emit({"type": "hello", "host": os.uname().nodename, "time": now(),
              "protocol_version": STREAM_PROTOCOL_VERSION})
        while True:
            for observation in input_observations():
                occurrence = observation["source_event_id"]
                if inputs.get(occurrence) != observation:
                    emit(observation)
                    inputs[occurrence] = observation
            seen = set()
            for path in JOBS_DIRECTORY.glob("*/job.json") if JOBS_DIRECTORY.exists() else []:
                job_id = path.parent.name
                signature = job_signature(path.parent)
                if ignored.get(job_id) == signature:
                    continue
                # Recheck processes until both runner and agent have ended.
                if signature == signatures.get(job_id) and not runner_states.get(job_id):
                    seen.add(job_id)
                    continue
                try:
                    job = json.loads(path.read_text())
                except (ValueError, OSError):
                    continue
                status = derive_status(job)
                if status in TERMINAL_STATUSES and job.get("updated_at", 0) < now() - horizon_seconds:
                    ignored[job_id] = signature
                    continue
                seen.add(job_id)
                alive = runner_alive(job) or process_alive(job.get("agent_pid"))
                if signature != signatures.get(job_id) or alive != runner_states.get(job_id):
                    emit({"type": "job", "job": job_summary(job, arguments.events)})
                signatures[job_id] = signature
                runner_states[job_id] = alive
            for job_id in set(signatures) - seen:
                signatures.pop(job_id)
                runner_states.pop(job_id, None)
                emit({"type": "removed", "id": job_id})
            if now() - last_session_scan >= arguments.session_interval:
                last_session_scan = now()
                current = tracker.scan()
                for session_id, session in current.items():
                    if sessions.get(session_id) != session:
                        emit({"type": "session", "session": session})
                for session_id in set(sessions) - set(current):
                    emit({"type": "session_removed", "id": session_id})
                sessions = current
            if now() - last_pipeline_scan >= PIPELINE_SCAN_INTERVAL:
                last_pipeline_scan = now()
                for message in pipelines.scan():
                    emit(message)
            if now() - last_heartbeat >= arguments.heartbeat:
                emit({"type": "heartbeat", "time": now()})
                last_heartbeat = now()
            sys.stdout.flush()
            time.sleep(arguments.interval)
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            os.close(sys.stdout.fileno())


def command_sessions(arguments: argparse.Namespace) -> None:
    sessions = SessionTracker().scan().values()
    emit({"host": os.uname().nodename, "time": now(),
          "sessions": sorted(sessions, key=lambda session: session["started_at"] or 0)})


def command_show(arguments: argparse.Namespace) -> None:
    job = read_job(arguments.job)
    summary = job_summary(job, arguments.events)
    summary["steps"] = job["steps"]
    emit(summary)


def command_events(arguments: argparse.Namespace) -> None:
    path = job_directory(arguments.job) / "events.jsonl"
    for line in tail_lines(path, arguments.lines):
        print(line, flush=True)
    if not arguments.follow:
        return
    with open(path) as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                print(line, end="", flush=True)
                continue
            if derive_status(read_job(arguments.job)) not in ("running", "queued"):
                return
            time.sleep(0.5)


def command_wait(arguments: argparse.Namespace) -> None:
    """Block until the job (or one step) stops running, then print its summary."""
    deadline = now() + arguments.timeout if arguments.timeout else None
    while True:
        job = read_job(arguments.job)
        if arguments.step is not None:
            steps = job["steps"]
            if arguments.step >= len(steps):
                fail(f"job has no step {arguments.step}")
            finished = steps[arguments.step]["status"] not in ("pending", "running")
        else:
            finished = derive_status(job) not in ("running", "queued") or (
                derive_status(job) == "queued" and not runner_alive(job))
        if finished:
            summary = job_summary(job, 0)
            summary["results"] = [{"index": step["index"], "title": step["title"], "status": step["status"],
                                   "result": step.get("result")} for step in job["steps"]]
            emit(summary)
            return
        if deadline and now() > deadline:
            fail("timeout", code=2)
        time.sleep(1)


def command_cancel(arguments: argparse.Namespace) -> None:
    with locked_job(arguments.job) as job:
        job["cancelled"] = True
        for step in job["steps"]:
            if step["status"] == "pending" and arguments.all_steps:
                step["status"] = "cancelled"
        agent_pid = job.get("agent_pid")
    if agent_pid:
        with contextlib.suppress(OSError):
            os.kill(agent_pid, signal.SIGTERM)
    append_event(arguments.job, {"kind": "job", "status": "cancelled", "summary": "cancelled by orchestrator"})
    emit(job_summary(read_job(arguments.job), 0))


def command_result(arguments: argparse.Namespace) -> None:
    job = read_job(arguments.job)
    indexes = [arguments.step] if arguments.step is not None else [step["index"] for step in job["steps"]]
    results = []
    for index in indexes:
        path = JOBS_DIRECTORY / job["id"] / f"result-{index}.md"
        results.append({"index": index, "status": job["steps"][index]["status"],
                        "text": path.read_text() if path.exists() else None})
    outbox = sorted(str(path.relative_to(JOBS_DIRECTORY / job["id"] / "outbox"))
                    for path in (JOBS_DIRECTORY / job["id"] / "outbox").rglob("*") if path.is_file())
    emit({"id": job["id"], "results": results, "outbox": outbox, "job_dir": str(JOBS_DIRECTORY / job["id"])})


def command_move(arguments: argparse.Namespace) -> None:
    with locked_job(arguments.job) as job:
        job["project"] = arguments.project
    emit(job_summary(read_job(arguments.job), 0))


def command_remove(arguments: argparse.Namespace) -> None:
    job = read_job(arguments.job)
    if derive_status(job) in ("running",):
        fail("job is running; cancel it first")
    shutil.rmtree(JOBS_DIRECTORY / arguments.job)
    emit({"removed": arguments.job})


def command_configure(arguments: argparse.Namespace) -> None:
    FLEET_HOME.mkdir(parents=True, exist_ok=True)
    JOBS_DIRECTORY.mkdir(exist_ok=True)
    config = load_config()
    config.update(json.loads(arguments.json))
    CONFIG_PATH.write_text(json.dumps(config, indent=1))
    missing = [name for name in ("claude", "codex") if not config.get(name)]
    emit({"host": os.uname().nodename, "config": config, "missing": missing,
          "tmux": shutil.which("tmux") is not None})


def main() -> None:
    parser = argparse.ArgumentParser(prog="fleetd")
    commands = parser.add_subparsers(dest="command", required=True)

    deliver = commands.add_parser("deliver")
    deliver.add_argument("job")
    deliver.add_argument("--key", required=True)
    deliver.add_argument("--schema-version", type=int, required=True)
    deliver.set_defaults(handler=command_deliver)

    grant = commands.add_parser("grant", help="allow Claude permission rules (JSON list on stdin) for a job")
    grant.add_argument("job")
    grant.add_argument("--step", type=int, required=True, help="the step whose refusals these rules answer")
    grant.add_argument("--key", required=True)
    grant.add_argument("--schema-version", type=int, required=True)
    grant.set_defaults(handler=command_grant)

    create = commands.add_parser("create")
    create.add_argument("--id")
    create.add_argument("--run-id")
    create.add_argument("--fingerprint")
    create.add_argument("--schema-version", type=int)
    create.add_argument("--project", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--agent", choices=("claude", "codex"), required=True)
    create.add_argument("--model")
    create.add_argument("--cwd", required=True)
    create.add_argument("--permission")
    create.add_argument("--steps-file", required=True)
    create.add_argument("--keep-going", action="store_true")
    create.add_argument("--hold", action="store_true")
    create.add_argument("--allowed-tools", help="JSON list of Claude permission rules, e.g. [\"Bash(ss:*)\"]")
    create.add_argument("--add-dir", action="append", default=[], help="extra directory the claude agent may use (repeatable)")
    create.add_argument("--env", action="append", default=[], help="NAME=value set in the agent's environment (repeatable)")
    create.set_defaults(handler=command_create)

    add = commands.add_parser("add")
    add.add_argument("job")
    add.add_argument("--steps-file", required=True)
    add.add_argument("--retry", action="store_true")
    add.add_argument("--hold", action="store_true")
    add.set_defaults(handler=command_add)

    start = commands.add_parser("start")
    start.add_argument("job")
    start.add_argument("--run-id")
    start.add_argument("--fingerprint")
    start.add_argument("--schema-version", type=int)
    start.set_defaults(handler=command_start)

    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("run_id")
    reconcile.add_argument("--fingerprint", required=True)
    reconcile.add_argument("--schema-version", type=int, required=True)
    reconcile.set_defaults(handler=command_reconcile)

    listing = commands.add_parser("ls")
    listing.add_argument("--all", action="store_true")
    listing.add_argument("--since-hours", type=float, default=24)
    listing.add_argument("--events", type=int, default=0)
    listing.set_defaults(handler=command_list)

    stream = commands.add_parser("stream")
    stream.add_argument("--since-hours", type=float, default=24)
    stream.add_argument("--events", type=int, default=15)
    stream.add_argument("--interval", type=float, default=0.3)
    stream.add_argument("--heartbeat", type=float, default=5)
    stream.add_argument("--session-interval", type=float, default=SESSION_SCAN_INTERVAL)
    stream.set_defaults(handler=command_stream)

    sessions = commands.add_parser("sessions", help="live interactive Claude Code / Codex CLI sessions")
    sessions.set_defaults(handler=command_sessions)

    show = commands.add_parser("show")
    show.add_argument("job")
    show.add_argument("--events", type=int, default=30)
    show.set_defaults(handler=command_show)

    events = commands.add_parser("events")
    events.add_argument("job")
    events.add_argument("--lines", type=int, default=40)
    events.add_argument("--follow", "-f", action="store_true")
    events.set_defaults(handler=command_events)

    wait = commands.add_parser("wait")
    wait.add_argument("job")
    wait.add_argument("--step", type=int)
    wait.add_argument("--timeout", type=float)
    wait.set_defaults(handler=command_wait)

    cancel = commands.add_parser("cancel")
    cancel.add_argument("job")
    cancel.add_argument("--all-steps", action="store_true")
    cancel.set_defaults(handler=command_cancel)

    read = commands.add_parser("read")
    read.add_argument("job")
    read.add_argument("document")
    read.set_defaults(handler=command_read)

    result = commands.add_parser("result")
    result.add_argument("job")
    result.add_argument("--step", type=int)
    result.set_defaults(handler=command_result)

    move = commands.add_parser("mv")
    move.add_argument("job")
    move.add_argument("project")
    move.set_defaults(handler=command_move)

    remove = commands.add_parser("rm")
    remove.add_argument("job")
    remove.set_defaults(handler=command_remove)

    configure = commands.add_parser("configure")
    configure.add_argument("json")
    configure.set_defaults(handler=command_configure)

    run = commands.add_parser("_run")
    run.add_argument("job")
    run.set_defaults(handler=lambda arguments: run_job(arguments.job))

    hook = commands.add_parser("input-hook", help="receive Claude permission hooks on stdin")
    hook.add_argument("--project", required=True)
    hook.add_argument("--job")
    hook.add_argument("--step-index", type=int)
    hook.set_defaults(handler=command_input_hook)

    settings = commands.add_parser("input-hook-settings", help="Claude --settings JSON for an interactive session")
    settings.add_argument("--project", required=True)
    settings.set_defaults(handler=lambda args: emit(input_hook_settings(args.project)))

    arguments = parser.parse_args()
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
