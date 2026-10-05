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
import base64
import collections
import contextlib
import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
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
TERMINAL_STATUSES = ("done", "failed", "blocked", "cancelled", "lost")
# Only these leave ls and the stream after --since-hours; failed and blocked jobs wait for someone.
AGED_STATUSES = ("done", "cancelled", "lost")
# `rm` deletes these without --force; others still hold work or a question.
REMOVABLE_STATUSES = ("done", "failed", "cancelled", "lost")
WORKER_VERSION = "0.1.0"
WIRE_PROTOCOL_VERSION = 1
STREAM_PROTOCOL_VERSION = 3
DISPATCH_SCHEMA_VERSION = 4
USAGE_SCHEMA_VERSION = 1
WORKSPACE_REFRESH_SECONDS = 30
GIT_TIMEOUT_SECONDS = 10

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


def closing_question(text: Any) -> Optional[str]:
    """The last sentence of a message that asks something, outside code blocks, or None."""
    prose = re.sub(r"```.*?(```|$)", "\n", str(text or ""), flags=re.S)
    for sentence in reversed(re.split(r"(?<=[.!?])\s+|\n+", prose)):
        sentence = sentence.strip().strip("*_").strip()
        if sentence.endswith("?"):
            return shorten(sentence)
    return None


def text_event(text: str) -> JsonObject:
    """An agent message: the summary is its opening, and ask is the question it leaves you with, if any."""
    event: JsonObject = {"kind": "text", "summary": shorten(text)}
    question = closing_question(text)
    if question:
        event["ask"] = question
    return event


def load_config() -> JsonObject:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def job_directory(job_id: str) -> Path:
    directory = JOBS_DIRECTORY / job_id
    if not (directory / "job.json").exists():
        fail(f"no such job: {job_id}")
    return directory


def expand_job_id(job_id: str) -> str:
    """A job's full id from a unique prefix of it; anything else comes back unchanged."""
    if not job_id or (JOBS_DIRECTORY / job_id / "job.json").exists() or not JOBS_DIRECTORY.is_dir():
        return job_id
    matches = [path.name for path in JOBS_DIRECTORY.iterdir()
               if path.name.startswith(job_id) and (path / "job.json").exists()]
    if len(matches) > 1:
        fail(f"'{job_id}' matches {len(matches)} jobs; use more of the id")
    return matches[0] if matches else job_id


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
    if any(step["status"] == "blocked" and step.get("answered_by") is None for step in steps):
        return "blocked"
    if any(step["status"] == "pending" for step in steps):
        return "queued"
    return "done"


# ------------------------------------------------------ event normalisation


MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")
# A step's final reply counts as a document only when it is a real write-up, not "OK".
REPORT_MINIMUM_BYTES = 400
DOCUMENT_READ_LIMIT = 2 * 1024 * 1024
# Images a document links to, served by `fleetd read-asset`; anything else is refused.
ASSET_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
ASSET_READ_LIMIT = 32 * 1024 * 1024


def is_markdown(path: str) -> bool:
    """A Markdown document; CLAUDE.local.md and other *.local.md files are private notes, not auto-recorded or previewed."""
    return path.lower().endswith(MARKDOWN_SUFFIXES) and not path.lower().endswith(".local.md")


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
                    events.append(text_event(block["text"]))
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
            return [{**text_event(item.get("text") or ""), "text": item.get("text")}]
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


# The user reads job documents in the Fleet reader, which renders fenced code and Mermaid inline.
WRITING_GUIDE = (
    "Markdown documents you write (reports, reviews, plans, notes) are read by a person in a reader that "
    "shows fenced code and ```mermaid diagrams inline. Lead with the conclusion, then the evidence. Make every "
    "claim concrete: name the file and line, quote a short code excerpt in a fenced block with its language, "
    "and show the command you ran with its relevant output. Give at least one specific example for each "
    "finding or recommendation. Draw a flow, structure or dependency as a Mermaid diagram rather than "
    "describing it in prose.\n"
)


def job_preamble(job: JsonObject) -> str:
    directory = JOBS_DIRECTORY / job["id"]
    return (
        f"You are running as fleet job {job['id']} (project: {job['project']}) on "
        f"{os.uname().nodename}, driven by an orchestrator you cannot talk to directly.\n"
        f"Job goal: {job['description']}\n"
        f"Context files from the orchestrator (read what is relevant): {directory / 'context'}\n"
        f"Put any files the orchestrator should collect in: {directory / 'outbox'}\n"
        "When you make images (screenshots, renders, charts), save them in the outbox and show the important ones "
        "in your step summary and reports with Markdown image syntax, e.g. `![Mock beside the concept](outbox/mock.png)` "
        "in the step summary, or a path relative to the report file. The deck displays them inline.\n"
        "When raising user attention with fleet attention add --owner user, include --reason saying why "
        "the user must act. Delegate existing items only within a confirmed project triage mandate; "
        "fleet attention delegate keeps them open and visible, and fleet attention take revokes agent ownership. "
        "Triage must not complete work or judge criteria.\n"
        f"{WRITING_GUIDE}"
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
                     "workspace-write": ["--sandbox", "workspace-write"],
                     "danger-full-access": ["--dangerously-bypass-approvals-and-sandbox"]}[job["permission"]]
    model_flags = ["--model", job["model"]] if job.get("model") else []
    writable_directories = [str(JOBS_DIRECTORY / job["id"]), *job.get("add_dirs", [])]
    if session_id:
        # `exec resume` has neither --sandbox nor --add-dir, so the job's sandbox and its job directory
        # (the outbox a later step writes to) are passed as config overrides.
        return [codex, "exec", "resume", "--json", "--skip-git-repo-check",
                "-c", f'sandbox_mode="{job["permission"]}"',
                "-c", f'sandbox_workspace_write.writable_roots={json.dumps(writable_directories)}',
                *model_flags, session_id, prompt]
    add_dir_flags = [flag for directory in writable_directories for flag in ("--add-dir", directory)]
    return [codex, "exec", "--json", "--skip-git-repo-check", *sandbox_flags, *model_flags,
            "-C", job["cwd"], *add_dir_flags, prompt]


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
        if self.name != "claude" and allow:
            raise ValueError("--allow applies to claude jobs only (codex uses its sandbox)")
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


STATUS_LINE = re.compile(r"FLEET_STATUS:\s*\**\s*(done|blocked|failed)\b\**[ \t]*(?:[—–:-]+[ \t]*([^\n]*))?",
                         re.IGNORECASE)


def reported_status(text: str) -> Optional[str]:
    """The agent's own verdict from its last FLEET_STATUS line; None when it gave none."""
    matches = STATUS_LINE.findall(text or "")
    return matches[-1][0].lower() if matches else None


def reported_reason(text: str) -> Optional[str]:
    """What the agent gave after the dash on its last FLEET_STATUS line; None when it gave nothing."""
    matches = STATUS_LINE.findall(text or "")
    if not matches:
        return None
    return matches[-1][1].strip().strip("*").strip() or None


def record_written_documents(job_id: str, cwd: str, paths: List[str], step_index: int) -> List[Tuple[int, str]]:
    """Remember Markdown files the agent wrote so the orchestrator and the deck can find them.

    Returns the (index, absolute path) of every given path, whether new or already known.
    """
    recorded = []
    with locked_job(job_id) as live_job:
        written = live_job.setdefault("written_documents", [])
        known = {entry["path"]: index for index, entry in enumerate(written)}
        for path in paths:
            absolute = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
            if absolute not in known:
                written.append({"path": absolute, "step": step_index})
                known[absolute] = len(written) - 1
            recorded.append((known[absolute], absolute))
    return recorded


def safe_document_source(source: Path) -> bool:
    """A document the agent wrote may be copied or read wherever it lives, if it is a regular Markdown file.

    Agents keep working notes outside their cwd too. A symlink is never followed.
    """
    return is_markdown(source.name) and not source.is_symlink() and source.is_file()


def copy_document(job_id: str, index: int, source: Path) -> Path:
    artifacts = JOBS_DIRECTORY / job_id / "artifacts"
    artifacts.mkdir(exist_ok=True)
    target = artifacts / f"file-{index}{source.suffix}"
    temporary = artifacts / f"file-{index}.tmp"
    shutil.copy2(source, temporary)
    temporary.replace(target)
    return target


def copy_written_documents(job_id: str) -> None:
    """Keep agent-written Markdown under the job's approved document root."""
    with locked_job(job_id) as live_job:
        for index, entry in enumerate(live_job.get("written_documents", [])):
            source = Path(entry["path"])
            if safe_document_source(source):
                entry["artifact"] = str(copy_document(job_id, index, source))


class DocumentMirror:
    """Copies recorded Markdown into the job's artifacts whenever the agent changes it, so it reads live mid-step."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.watched: Dict[int, Path] = {}
        self.copied: Dict[int, tuple] = {}
        self.announced: set = set()

    def watch(self, recorded: List[Tuple[int, str]]) -> None:
        for index, path in recorded:
            self.watched[index] = Path(path)

    def sync(self) -> None:
        """Stats the watched files; copies only those whose mtime or size changed since the last copy."""
        fresh: Dict[int, str] = {}
        for index, source in self.watched.items():
            try:
                stat = source.lstat()
            except OSError:
                continue
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self.copied.get(index) or not safe_document_source(source):
                continue
            try:
                target = copy_document(self.job_id, index, source)
            except OSError:
                continue
            self.copied[index] = signature
            if index not in self.announced:
                fresh[index] = str(target)
        if fresh:
            with locked_job(self.job_id) as live_job:
                written = live_job.get("written_documents", [])
                for index, target in fresh.items():
                    written[index]["artifact"] = target
            self.announced.update(fresh)


def write_briefs(job_id: str, steps: List[JsonObject]) -> None:
    """Each step's prompt as a readable document, there from the moment the step exists."""
    for step in steps:
        path = JOBS_DIRECTORY / job_id / f"brief-{step['index']}.md"
        if not path.exists():
            path.write_text(step["prompt"])


# ---------------------------------------------------------------- workspace


def git(cwd: str, *arguments: str) -> subprocess.CompletedProcess:
    # Optional locks off: a status read must never take index.lock from under the agent's own git commands.
    return subprocess.run(["git", "-C", cwd, *arguments], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=GIT_TIMEOUT_SECONDS, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})


def collect_workspace(cwd: str) -> Tuple[Optional[JsonObject], Optional[str]]:
    """The git checkout cwd is in, or None and why not; nothing is filled in that git did not report.

    `toplevel` is the checkout's root, `repository` the main repository a linked worktree belongs to (the
    checkout itself otherwise, or the git directory of a bare one), `branch` None when HEAD is detached, `head`
    None on a branch with no commit yet, and `dirty` the count of changed and untracked paths."""
    if not os.path.isdir(cwd):
        return None, "working directory is missing"
    try:
        located = git(cwd, "rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir")
        if located.returncode != 0:
            message = located.stderr.strip()
            return None, "not a git repository" if "not a git repository" in message else f"git: {shorten(message)}"
        toplevel, git_directory, common = located.stdout.splitlines()
        status = git(cwd, "status", "--porcelain=v2", "--branch")
        if status.returncode != 0:
            return None, f"git status: {shorten(status.stderr.strip())}"
        headers = dict(line[2:].split(" ", 1) for line in status.stdout.splitlines() if line.startswith("# "))
        oid, branch = headers["branch.oid"], headers["branch.head"]
        head = None
        if oid != "(initial)":
            short = git(cwd, "rev-parse", "--short", oid)
            if short.returncode != 0:
                return None, f"git rev-parse: {shorten(short.stderr.strip())}"
            head = short.stdout.strip()
    except FileNotFoundError:
        return None, "git is not installed"
    except subprocess.TimeoutExpired:
        return None, f"git took longer than {GIT_TIMEOUT_SECONDS} s"
    common = os.path.realpath(os.path.join(cwd, common))
    return {"toplevel": toplevel,
            "linked_worktree": os.path.realpath(git_directory) != common,
            "repository": os.path.dirname(common) if os.path.basename(common) == ".git" else common,
            "branch": None if branch == "(detached)" else branch, "detached": branch == "(detached)", "head": head,
            "dirty": sum(not line.startswith("#") for line in status.stdout.splitlines()),
            "collected_at": now()}, None


def refresh_workspace(job_id: str, cwd: str) -> None:
    """Record the job's workspace as it is now; any fault becomes its reason rather than costing the step."""
    try:
        workspace, reason = collect_workspace(cwd)
    except Exception as error:  # noqa: BLE001 — reported, never raised into the runner
        workspace, reason = None, f"workspace collection failed: {error}"
    with locked_job(job_id) as job:
        job["workspace"], job["workspace_reason"] = workspace, reason


def checked_git(cwd: str, *arguments: str) -> str:
    result = git(cwd, *arguments)
    if result.returncode != 0:
        raise ValueError(f"git {arguments[0]}: {shorten(result.stderr.strip())}")
    return result.stdout.strip()


def git_branch(cwd: str) -> Optional[str]:
    result = git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    if result.returncode == 1:
        return None  # detached HEAD
    if result.returncode != 0:
        raise ValueError(f"git symbolic-ref: {shorten(result.stderr.strip())}")
    return result.stdout.strip()


def git_ancestor(cwd: str, base: str, head: str) -> bool:
    result = git(cwd, "merge-base", "--is-ancestor", base, head)
    if result.returncode not in (0, 1):
        raise ValueError(f"git merge-base: {shorten(result.stderr.strip())}")
    return result.returncode == 0


def begin_step_git(cwd: str) -> JsonObject:
    """Capture HEAD before the agent starts; retain reflog offsets locally to exclude earlier same-second pushes."""
    record: JsonObject = {"at": now()}
    try:
        record["base"] = checked_git(cwd, "rev-parse", "--verify", "HEAD")
        record["branch"] = git_branch(cwd)
        common = Path(os.path.realpath(os.path.join(cwd, checked_git(cwd, "rev-parse", "--git-common-dir"))))
        logs = common / "logs" / "refs" / "remotes"
        record["_reflog_offsets"] = {str(path.relative_to(common)): path.stat().st_size
                                     for path in logs.rglob("*") if path.is_file()}
    except Exception as error:  # capture must never cost the agent's step
        record["reason"] = f"step git capture failed: {error}"
    return record


def end_step_git(cwd: str, beginning: JsonObject) -> JsonObject:
    """Capture base..head and remote-tracking pushes; errors leave the available evidence and a reason."""
    record = {key: value for key, value in beginning.items() if not key.startswith("_")}
    record["ended_at"] = now()
    if "reason" in record:
        return record
    try:
        base = record["base"]
        head = record["head"] = checked_git(cwd, "rev-parse", "--verify", "HEAD")
        record["branch_end"] = git_branch(cwd)
        record["base_is_ancestor"] = git_ancestor(cwd, base, head)
        record["commit_count"] = int(checked_git(cwd, "rev-list", "--count", f"{base}..{head}"))
        lines = checked_git(cwd, "log", "--no-show-signature", "--reverse", "--max-count=100",
                            "--format=%H%x09%at%x09%s", f"{base}..{head}")
        record["commits"] = [{"sha": sha, "at": int(at), "subject": subject[:72]}
                             for sha, at, subject in (line.split("\t", 2) for line in lines.splitlines())]
        record["truncated"] = record["commit_count"] > len(record["commits"])
        common = Path(os.path.realpath(os.path.join(cwd, checked_git(cwd, "rev-parse", "--git-common-dir"))))
        pushes = record["pushes"] = []
        for path in sorted((common / "logs" / "refs" / "remotes").rglob("*")):
            if not path.is_file():
                continue
            offset = beginning["_reflog_offsets"].get(str(path.relative_to(common)), 0)
            size = path.stat().st_size
            if size < offset:
                raise ValueError(f"remote-tracking reflog shortened during step: {path.relative_to(common)}")
            if size == offset:
                continue
            with path.open("rb") as handle:
                handle.seek(offset)
                entries = handle.read(size - offset).decode(errors="replace").splitlines()
            for entry in entries:
                metadata, message = entry.split("\t", 1)
                if not message.startswith("update by push"):
                    continue
                old, new = metadata.split(" ", 2)[:2]
                at = int(metadata.rsplit(" ", 2)[1])
                if not int(record["at"]) <= at <= int(record["ended_at"]):
                    continue
                # Check the full range, including commits omitted from the bounded display list.
                if new == head or (git_ancestor(cwd, new, head) and not git_ancestor(cwd, new, base)):
                    pushes.append({"ref": path.relative_to(common / "logs").as_posix(),
                                   "old": old, "new": new, "at": at})
    except Exception as error:  # capture must never cost the agent's step
        record["reason"] = f"step git capture failed: {error}"
    return record


def step_git_record(step: JsonObject, job_status: Optional[str] = None) -> JsonObject:
    """Public git facts; local reflog offsets never enter the stream or controller history."""
    if "git" not in step:
        return {"reason": "not recorded: this step has no git capture"}
    record = {key: value for key, value in step["git"].items() if not key.startswith("_")}
    if "base" in record and "ended_at" not in record and "reason" not in record and job_status in (
            "lost", "cancelled", "failed", "done", "blocked"):
        record["reason"] = "step ended without its runner; end not recorded"
    return record


class WorkspaceWatch:
    """Refreshes a running step's workspace every WORKSPACE_REFRESH_SECONDS, off the runner's own thread."""

    def __init__(self, job_id: str, cwd: str) -> None:
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.loop, args=(job_id, cwd), daemon=True)

    def loop(self, job_id: str, cwd: str) -> None:
        while not self.stopped.wait(WORKSPACE_REFRESH_SECONDS):
            refresh_workspace(job_id, cwd)

    def __enter__(self) -> "WorkspaceWatch":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stopped.set()
        self.thread.join()


def _run_step_attempt(job: JsonObject, step: JsonObject) -> JsonObject:
    job_id = job["id"]
    config = load_config()
    if job["agent"] in config and not executable_path(config[job["agent"]]):
        reason = f"{job['agent']} runtime binary is missing or not executable: {config[job['agent']]!r}; set it with fleet install HOST --{job['agent']} PATH"
        return {"ok": False, "summary": reason, "reason": reason, "text": "", "usage": None}
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
    runtime_error = ""
    mirror = DocumentMirror(job_id)
    raw_path = JOBS_DIRECTORY / job_id / f"raw-{step['index']}.jsonl"
    refusals = StreamRefusals(job, step["index"]) if job["agent"] == "claude" else None
    refresh_workspace(job_id, job["cwd"])
    with open(raw_path, "a") as raw_file, WorkspaceWatch(job_id, job["cwd"]):
        try:
            process = subprocess.Popen(command, cwd=job["cwd"], env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as error:
            reason = f"cannot launch {job['agent']} runtime binary {command[0]!r}: {error}"
            return {**outcome, "summary": reason, "reason": reason}
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
            if refusals is not None:
                # an unexpected record shape must not cost the step
                with contextlib.suppress(OSError, ValueError, TypeError, KeyError, AttributeError):
                    refusals.consume(record)
            events, result = runtime.parse(record)
            # Only runtime errors qualify, never failed tools or agent-authored text.
            if record.get("type") in ("error", "turn.failed"):
                runtime_error = next((event["summary"] for event in events if event["kind"] == "error"), "")
            elif job["agent"] == "claude" and record.get("type") == "result" and record.get("is_error"):
                runtime_error = " ".join(str(value) for value in [record.get("result") or "", *(record.get("errors") or [])])
            if result is not None:
                if result.get("ok"):
                    runtime_error = ""
                if record.get("session_id"):
                    with locked_job(job_id) as live_job:
                        live_job["session_id"] = record["session_id"]
                    job["session_id"] = record["session_id"]
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
                    mirror.watch(record_written_documents(job_id, job["cwd"], event["paths"], step["index"]))
                if event["kind"] == "text":
                    last_text = event.pop("text", None) or event["summary"]
                append_event(job_id, event)
            # A Claude write lands after its tool_use line, so every later line checks again.
            mirror.sync()
        exit_code = process.wait()
    refresh_workspace(job_id, job["cwd"])
    runtime.finish(outcome, exit_code, last_text)
    if runtime_error:
        outcome.update(ok=False, summary=runtime_error, text=runtime_error, runtime_error=runtime_error)
    outcome["exit_code"] = exit_code
    if exit_code < 0 and not result_recorded:
        outcome["reason"] = "lost"
    reported = reported_status(outcome.get("text") or outcome["summary"])
    if reported is not None:
        outcome["reported_status"] = reported
        if reported == "blocked" and outcome["ok"]:
            # The agent finished its turn and says it needs the supervisor: not broken work.
            outcome["blocked"] = True
            reason = reported_reason(outcome.get("text") or outcome["summary"])
            if reason is not None:
                outcome["reason"] = reason
        if reported != "done":
            outcome["ok"] = False
    copy_written_documents(job_id)
    return outcome


def transient_runtime_reason(message: str) -> Optional[str]:
    """Recognize provider failures shared by Codex and Claude runtime envelopes."""
    lowered = message.lower()
    if "model is at capacity" in lowered:
        return "model at capacity"
    if "overloaded" in lowered or "overload_error" in lowered:
        return "provider overloaded"
    if "rate limit" in lowered or "rate_limit" in lowered or "too many requests" in lowered:
        return "provider rate limited"
    if re.search(r"(?:http(?: status)?|status(?: code)?|response(?: status)?|api error)[: =]+(?:429|5\d\d)\b", lowered):
        return "provider HTTP error"
    if "api_error" in lowered or "internal server error" in lowered or "service unavailable" in lowered:
        return "provider unavailable"
    return None


def _run_step_with_retries(job: JsonObject, step: JsonObject) -> JsonObject:
    """Resume transiently failed turns with bounded, observable, cancellable waits."""
    delays = load_config().get("runtime_retry_delays", [60, 180, 600])
    if not isinstance(delays, list) or len(delays) > 3 or any(
            not isinstance(delay, (int, float)) or isinstance(delay, bool) or not math.isfinite(delay) or delay < 0 for delay in delays):
        return {"ok": False, "summary": "runtime_retry_delays must contain at most three finite nonnegative delays in seconds",
                "reason": "invalid runtime retry configuration"}
    retries = 0
    while True:
        outcome = _run_step_attempt(job, step)
        reason = transient_runtime_reason(outcome.get("runtime_error", ""))
        if outcome["ok"] or reason is None:
            return outcome
        if retries == len(delays):
            outcome.update(reason=f"{reason}; exhausted {retries} runtime retries",
                           summary=f"{reason}; exhausted {retries} runtime retries: {outcome['summary']}")
            return outcome
        retry_at = now() + delays[retries]
        retries += 1
        waiting = {"kind": "retry", "step": step["index"], "reason": reason,
                   "retry": retries, "retry_limit": len(delays), "retry_at": retry_at,
                   "summary": f"waiting: {reason}, retry {retries}/{len(delays)} at {time.strftime('%H:%M', time.localtime(retry_at))}"}
        with locked_job(job["id"]) as live_job:
            live_job["agent_pid"] = None
            live_job["runtime_wait"] = waiting
        append_event(job["id"], waiting)
        while True:
            live_job = read_job(job["id"])
            if live_job.get("cancelled") or now() >= retry_at:
                break
            time.sleep(max(0, min(0.5, retry_at - now())))
        with locked_job(job["id"]) as live_job:
            live_job.pop("runtime_wait", None)
            job["session_id"] = live_job.get("session_id")
            cancelled = live_job.get("cancelled")
        if cancelled:
            return outcome
        append_event(job["id"], {"kind": "retry", "step": step["index"],
                                 "summary": f"resuming after {reason}, retry {retries}/{len(delays)}"})


def inject_decisions(job: JsonObject, step: JsonObject) -> None:
    shown = set(job.get("shown_decisions", []))
    pending = [d for d in job.get("decisions_since_dispatch", []) if d["id"] not in shown]
    if not pending:
        return
    text = "\n\n".join(f"Question: {d['question']}\nAnswer: {d['answer']}\nActor: {d['actor']}\nPrinciple: {d['principle']}" for d in pending)
    step["prompt"] = f"## Decisions recorded since this job started\n\n{text}\n\n{step['prompt']}"
    step["shown_decisions"] = [d["id"] for d in pending]
    job.setdefault("shown_decisions", []).extend(step["shown_decisions"])
    # Use the same document path as dispatch and appended steps.
    (JOBS_DIRECTORY / job["id"] / f"brief-{step['index']}.md").write_text(step["prompt"])


def run_step(job: JsonObject, step: JsonObject) -> JsonObject:
    # The git evidence spans the entire step, including work before a provider failure.
    step_git = begin_step_git(job["cwd"])
    with locked_job(job["id"]) as live_job:
        live_job["steps"][step["index"]]["git"] = step_git
    try:
        return _run_step_with_retries(job, step)
    finally:
        step_git = end_step_git(job["cwd"], step_git)
        with locked_job(job["id"]) as live_job:
            live_job["steps"][step["index"]]["git"] = step_git


def next_step(job: JsonObject) -> Optional[JsonObject]:
    """The step the runner starts next: none while a step waits for its answer, else a step answering a blocked
    one before the rest, else the first pending step. Steps keep their indices, which name their files."""
    steps = job["steps"]
    if any(step["status"] == "blocked" and step.get("answered_by") is None for step in steps):
        return None
    answering = {step["answered_by"] for step in steps if step.get("answered_by") is not None}
    answering |= {index for added in job.get("keyed_additions", []) if added.get("answers") is not None
                  for index in added["steps"]}
    pending = [step for step in steps if step["status"] == "pending"]
    return next((step for step in pending if step["index"] in answering), pending[0] if pending else None)


def run_job(job_id: str) -> None:
    """Runner loop: executes pending steps, answers first, until none remain or a step waits for an answer."""
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
                step = next_step(job)
                if step is None:
                    break
                inject_decisions(job, step)
                step["status"] = "running"
                step["started_at"] = now()
                job["todos"] = []
            outcome = run_step(job, step)
            with locked_job(job_id) as job:
                live_step = job["steps"][step["index"]]
                if job.get("cancelled"):
                    live_step["status"] = "cancelled"
                else:
                    live_step["status"] = "done" if outcome["ok"] else "blocked" if outcome.get("blocked") else "failed"
                live_step["finished_at"] = now()
                live_step["result"] = outcome["summary"]
                live_step["usage"] = outcome.get("usage")
                if "reason" in outcome:
                    live_step["reason"] = outcome["reason"]
                (JOBS_DIRECTORY / job_id / f"result-{step['index']}.md").write_text(outcome.get("text") or outcome["summary"])
                job["agent_pid"] = None
                # A blocked step holds the job whatever stop_on_failure says: the rest waits for the answer.
                stop = live_step["status"] == "blocked" or (live_step["status"] != "done"
                                                            and job.get("stop_on_failure", True))
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
    # Multiple command arguments make tmux exec directly. Its default shell can
    # run user startup hooks; only a plain POSIX shell is needed for this pipe.
    subprocess.run([*TMUX_COMMAND, "new-session", "-d", "-s", session, "-c", job["cwd"],
                    "/bin/sh", "-c", f"{runner_command} 2>&1 | tee -a {shlex.quote(str(log_path))}"], check=True,
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
    activity = job.get("runtime_wait") or next((event for event in reversed(events) if event.get("kind") in ("tool", "text", "error")), None)
    return {
        "id": job["id"], "host": os.uname().nodename, "project": job["project"],
        "schema_version": DISPATCH_SCHEMA_VERSION, "run_id": job.get("run_id"),
        "usage_schema_version": USAGE_SCHEMA_VERSION, "usage": _runtime(job["agent"]).usage(job["steps"]),
        "fingerprint": job.get("fingerprint"), "start_requested": job.get("start_requested"),
        "description": job["description"], "agent": job["agent"], "model": job.get("model"),
        "cwd": job["cwd"], "permission": job["permission"], "status": status,
        # Jobs created before workspaces were collected have neither key until their next step.
        "workspace": job.get("workspace"),
        "workspace_reason": job["workspace_reason"] if "workspace_reason" in job else "not collected yet",
        "created_at": job["created_at"], "updated_at": job.get("updated_at"),
        "steps": [{**{key: step.get(key) for key in ("index", "title", "status", "started_at", "finished_at", "result",
                                                     "answered_by", "work_item")},
                   "message": blocked_message(job["id"], step), "git": step_git_record(step, status)} for step in job["steps"]],
        "todos": job.get("todos", []),
        # Decisions the job's agent recorded here, for the controller to take into its store (see command_decision).
        "decisions": job.get("decisions", []),
        "decisions_since_dispatch": job.get("decisions_since_dispatch", []),
        "shown_decisions": job.get("shown_decisions", []),
        "activity": activity,
        "events": events[-event_count:] if event_count else [],
        "session_id": job.get("session_id"),
        "tmux": shlex.join([*TMUX_COMMAND[:3], "attach", "-t", tmux_session(job['id'])]),
        "documents": job_documents(job),
        "trace": trace_summary(job["id"]),
    }


def blocked_message(job_id: str, step: JsonObject) -> Optional[str]:
    """A blocked step's final message in full (its result is shortened): what the agent asks the supervisor.

    None for any other step, an answered one, or when the step left no report.
    """
    if step["status"] != "blocked" or step.get("answered_by") is not None:
        return None
    with contextlib.suppress(OSError):
        return (JOBS_DIRECTORY / job_id / f"result-{step['index']}.md").read_text() or None
    return None


def job_documents(job: JsonObject) -> List[JsonObject]:
    """Documents the job was given and produced, including every file in its outbox.

    Only these can be read back with `fleetd read`, so the deck can never be used
    to fetch arbitrary files from the host.
    """
    directory = JOBS_DIRECTORY / job["id"]
    documents: List[JsonObject] = []

    def describe(document_id: str, path: Path, kind: str, name: str, step: Optional[int],
                 display_path: Optional[str] = None, include_empty: bool = False) -> None:
        with contextlib.suppress(OSError):
            stat = path.stat()
            if (stat.st_size or include_empty) and path.is_file():
                document = {"id": document_id, "kind": kind, "name": name, "step": step,
                            "path": display_path or str(path), "size": stat.st_size,
                            "mtime": round(stat.st_mtime, 3)}
                if display_path is not None:
                    document["read_path"] = str(path)
                documents.append(document)

    for step in job["steps"]:
        describe(f"brief-{step['index']}", directory / f"brief-{step['index']}.md", "brief",
                 f"Step {step['index'] + 1} brief", step["index"])
    context = directory / "context"
    if context.exists():
        for path in sorted(context.rglob("*")):
            if is_markdown(path.name):
                describe(f"context-{path.relative_to(context)}", path, "context", str(path.relative_to(context)), None)
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
            if path.is_file():
                describe(f"outbox-{path.relative_to(outbox)}", path, "outbox", str(path.relative_to(outbox)), None,
                         include_empty=True)
    for document in documents:
        if Path(document["path"]).suffix.lower() in ASSET_TYPES:
            document["media"] = "image"
        elif document["kind"] == "outbox" and not is_markdown(document["path"]):
            document["media"] = "file"
    return documents


def document_roots(job: JsonObject) -> List[Path]:
    """The job directory, the configured document_roots, and the job's project library root.

    A library root is configured with `fleet library add` on the machine running fleet; when that
    is this machine, its config is here too.
    """
    roots = [JOBS_DIRECTORY / job["id"], *(Path(root).expanduser() for root in load_config().get("document_roots", []))]
    client_config = Path(os.environ.get("FLEET_CONFIG") or Path.home() / ".config" / "fleet" / "config.json")
    with contextlib.suppress(OSError, ValueError, AttributeError, TypeError, KeyError):
        library = json.loads(client_config.read_text()).get("libraries", {}).get(job.get("project"))
        if library:   # a path, or {"path": ..., "recursive": true}
            roots.append(Path(library if isinstance(library, str) else library["path"]).expanduser())
    return roots


def approved(job: JsonObject, path: Path) -> bool:
    return any(path.is_relative_to(root.resolve()) for root in document_roots(job))


def listed_document(job: JsonObject, document_id: str) -> JsonObject:
    document = next((item for item in job_documents(job) if item["id"] == document_id), None)
    if document is None:
        fail(f"job {job['id']} has no document {document_id}")
    return document


def command_read(arguments: argparse.Namespace) -> None:
    job = read_job(arguments.job)
    document = listed_document(job, arguments.document)
    recorded = Path(document.pop("read_path", document["path"]))
    path = recorded.resolve()
    # A file the agent itself wrote is readable wherever it lives; anything else only under a root.
    written = document["kind"] == "file" and safe_document_source(recorded)
    if not written and not approved(job, path):
        fail(f"document path outside approved document roots: {document['path']}")
    if document.get("media") == "image":  # shown as a page holding the image, which loads through read-asset
        document.update({"truncated": False, "content": f"![{document['name']}](<{path.name}>)\n",
                         "job": job["id"], "project": job["project"], "agent": job["agent"],
                         "host": os.uname().nodename, "job_description": job["description"]})
        emit(document)
        return
    if document.get("media") == "file":
        document.update({"truncated": False,
                         "content": "The reader cannot preview this file type. It is listed for collection.\n\n"
                                    f"Fetch this job’s outbox with:\n\n```sh\nfleet pull HOST:{job['id']}\n```\n\nReplace HOST with the configured fleet host name.\n",
                         "job": job["id"], "project": job["project"], "agent": job["agent"],
                         "host": os.uname().nodename, "job_description": job["description"]})
        emit(document)
        return
    with open(path, "rb") as handle:
        raw = handle.read(DOCUMENT_READ_LIMIT + 1)
    document["truncated"] = len(raw) > DOCUMENT_READ_LIMIT
    document["content"] = raw[:DOCUMENT_READ_LIMIT].decode(errors="replace")
    document.update({"job": job["id"], "project": job["project"], "agent": job["agent"],
                     "host": os.uname().nodename, "job_description": job["description"]})
    emit(document)


def command_read_asset(arguments: argparse.Namespace) -> None:
    """An image a job document links to, resolved beside the document where the agent wrote it,
    under the same approved roots as the document itself."""
    job = read_job(arguments.job)
    document = listed_document(job, arguments.document)
    if "\x00" in arguments.path:
        fail(f"asset path outside approved document roots: {arguments.path}")
    # Relative paths resolve beside the document; absolute ones (agents often write them) stand as they are.
    path = (Path(document["path"]).parent / arguments.path).resolve()
    if not approved(job, path):
        fail(f"asset path outside approved document roots: {arguments.path}")
    content_type = ASSET_TYPES.get(path.suffix.lower())
    if content_type is None:
        fail(f"asset is not a supported image type: {arguments.path}")
    if not path.is_file():
        fail(f"job {job['id']} has no asset {arguments.path}")
    with open(path, "rb") as handle:
        raw = handle.read(ASSET_READ_LIMIT + 1)
    if len(raw) > ASSET_READ_LIMIT:
        fail(f"asset larger than {ASSET_READ_LIMIT} bytes: {arguments.path}")
    emit({"path": arguments.path, "type": content_type, "size": len(raw),
          "content": base64.b64encode(raw).decode("ascii")})


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
SESSION_RECORDS_DIRECTORY = FLEET_HOME / "sessions"
REMOVALS_DIRECTORY = FLEET_HOME / "removals"


def session_workspace(transcript: "Transcript", status: str) -> Tuple[Optional[JsonObject], Optional[str]]:
    """First observed base survives stream restarts; the workspace refreshes while work continues."""
    record = transcript.workspace_record
    if record is None or (status == "working" and now() - record["observed_at"] >= WORKSPACE_REFRESH_SECONDS):
        try:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", transcript.id) or transcript.id in (".", ".."):
                raise ValueError("invalid session identity")
            SESSION_RECORDS_DIRECTORY.mkdir(parents=True, exist_ok=True)
            path = SESSION_RECORDS_DIRECTORY / f"{transcript.id}.json"
            with open(path.with_suffix(".lock"), "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                previous = json.loads(path.read_text()) if path.exists() else None
                workspace, reason = collect_workspace(transcript.cwd)
                if previous is None:
                    base = checked_git(transcript.cwd, "rev-parse", "--verify", "HEAD") if workspace and workspace["head"] else None
                    base_at = now()
                    base_reason = (reason or "repository has no commits") if base is None else None
                else:
                    base, base_at, base_reason = previous["base"], previous["base_at"], previous["base_reason"]
                record = {"base": base, "base_at": base_at, "base_reason": base_reason, "workspace": workspace,
                          "workspace_reason": reason, "observed_at": now()}
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(record))
                temporary.replace(path)
                transcript.workspace_record = record
        except Exception as error:
            return None, f"session workspace collection failed: {error}"
    workspace = record["workspace"]
    if workspace is not None:
        workspace = {**workspace, "base": record["base"], "base_at": record["base_at"], "base_reason": record["base_reason"]}
    return workspace, record["workspace_reason"]
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
            return [text_event(text)] if text.strip() else []
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
        # The newest record's own timestamp: the file's mtime also moves when nothing is written to it.
        self.last_record_at: Optional[float] = None
        self.titles: Dict[str, str] = {}
        self.hidden = False  # a sub-agent's transcript or a fleet job's own session
        self.events: Deque[JsonObject] = collections.deque(maxlen=SESSION_EVENTS)
        self.activity: Optional[JsonObject] = None
        self.todos: List[JsonObject] = []
        self.workspace_record: Optional[JsonObject] = None

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
        stamp = parse_timestamp(record.get("timestamp")) if record.get("timestamp") else None
        if stamp is not None and (self.last_record_at is None or stamp > self.last_record_at):
            self.last_record_at = stamp
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
        workspace, workspace_reason = session_workspace(self, status)
        return {
            "id": self.id, "host": os.uname().nodename, "agent": self.agent, "cwd": self.cwd,
            "project": repository_name(self.cwd) if self.cwd else None,
            "title": shorten(title, SESSION_TITLE_LENGTH) if title else None,
            "status": status, "started_at": self.started_at, "updated_at": updated_at, "model": self.model,
            "todos": self.todos, "activity": self.activity, "events": list(self.events),
            "workspace": workspace, "workspace_reason": workspace_reason,
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

    def __init__(self, since: Optional[float] = None) -> None:
        self.transcripts: Dict[Path, Transcript] = {}
        self.job_sessions: Dict[Path, Tuple[int, Optional[str]]] = {}
        self.since = since

    def candidates(self) -> Iterator[Tuple[Path, str, os.stat_result]]:
        horizon = max(self.since, time.time() - 30 * 86400) if self.since is not None else time.time() - SESSION_ACTIVE_SECONDS
        # Only top-level transcripts: sub-agents write theirs in a subdirectory of the session.
        for project in scan_directory(CLAUDE_PROJECTS_DIRECTORY):
            for entry in scan_directory(Path(project.path)) if project.is_dir() else []:
                if entry.name.endswith(".jsonl"):
                    with contextlib.suppress(OSError):
                        stat = entry.stat()
                        if stat.st_mtime >= horizon:
                            yield Path(entry.path), "claude", stat
        today = datetime.date.today()
        days = min(31, int((time.time() - horizon) / 86400) + 2)
        for day in (today - datetime.timedelta(days=index) for index in range(days)):
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
            active = min(stat.st_mtime, transcript.last_record_at or stat.st_mtime)
            if clock - active >= SESSION_ACTIVE_SECONDS:
                if self.since is None:
                    continue
                status = "stopped"
            else:
                status = "working" if clock - active < SESSION_WORKING_SECONDS else "idle"
            sessions[transcript.id] = transcript.summary(status, round(active, 3))
        moved = session_projects()
        for identity in sessions.keys() & moved.keys():
            sessions[identity]["project"] = moved[identity]
        self.transcripts = live
        return sessions


SESSION_PROJECTS_PATH = FLEET_HOME / "session-projects.json"


def session_projects() -> Dict[str, str]:
    """Sessions moved to a project other than their repository's, by `fleet mv`: session id → project label."""
    try:
        return json.loads(SESSION_PROJECTS_PATH.read_text())
    except (OSError, ValueError):
        return {}


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


QUESTION_TOOL = "AskUserQuestion"


def record_input_hook(record: JsonObject, *, project: str, job_id: Optional[str] = None,
                      step_index: Optional[int] = None) -> None:
    """Keep a permission request or a question to the user until PostToolUse shows it was answered."""
    event = record["hook_event_name"]
    if event not in ("PermissionRequest", "PreToolUse", "PostToolUse"):
        return
    if event == "PreToolUse" and record.get("tool_name") != QUESTION_TOOL:
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
        if event in ("PermissionRequest", "PreToolUse"):
            if match is not None:
                return
            records.append({"type": "input_observation", "schema_version": 1,
                            "host": os.uname().nodename, "runtime": "claude",
                            "owner_type": "job" if job_id is not None else "session",
                            "job_id": job_id, "session_id": session_id, "step_index": step_index,
                            "project": project, "kind": "input_requested",
                            "reason": "permission" if event == "PermissionRequest" else "question",
                            "source_event": event, "source_event_id": secrets.token_hex(16),
                            "observed_at": now(), "context_reference": str(path),
                            "cwd": record.get("cwd"), "raw_request": record,
                            "denied_by": deny_rules_matching(record.get("tool_name"), record.get("tool_input") or {},
                                                             record.get("cwd")) if event == "PermissionRequest" else []})
        elif match is not None:
            match.update(kind="input_cleared", source_event=event, observed_at=now(), raw_resume=record)
        else:
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records))
        temporary.replace(path)


class StreamRefusals:
    """Records the refusals a job's Claude reports in its stream-json output.

    In -p mode Claude does not always run the PermissionRequest hook before refusing: some
    refusals (e.g. decision_reason_type subcommandResults) show only as a `permission_denied`
    system event and in the result's `permission_denials`. Each is recorded as the hook would
    have recorded it, into the same per-step observations, so a refusal reported by both paths
    is one entry (record_input_hook matches on tool name and input).
    """

    def __init__(self, job: JsonObject, step_index: int) -> None:
        self.job, self.step_index = job, step_index
        self.tool_uses: Dict[str, Tuple[str, JsonObject]] = {}   # tool_use_id → (tool name, input)
        self.recorded: set = set()

    def consume(self, record: JsonObject) -> None:
        kind = record.get("type")
        if kind == "assistant":
            for block in (record.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                    self.tool_uses[block.get("id")] = (block.get("name"), block["input"])
        elif kind == "system" and record.get("subtype") == "permission_denied":
            tool_use = self.tool_uses.get(record.get("tool_use_id"))
            if tool_use is not None:   # otherwise the result's permission_denials names it
                self.refused(record, record.get("tool_use_id"), record.get("tool_name") or tool_use[0], tool_use[1])
        elif kind == "result":
            for denial in record.get("permission_denials") or []:
                if isinstance(denial, dict) and isinstance(denial.get("tool_input"), dict):
                    self.refused(record, denial.get("tool_use_id"), denial.get("tool_name"), denial["tool_input"])

    def refused(self, record: JsonObject, tool_use_id: Optional[str], tool_name: Optional[str],
                tool_input: JsonObject) -> None:
        session_id = record.get("session_id") or self.job.get("session_id")
        if not tool_name or not session_id or (tool_use_id is not None and tool_use_id in self.recorded):
            return
        self.recorded.add(tool_use_id)
        record_input_hook({"hook_event_name": "PermissionRequest", "session_id": session_id, "cwd": self.job["cwd"],
                           "tool_name": tool_name, "tool_input": tool_input, "tool_use_id": tool_use_id,
                           "reported_by": "stream"},
                          project=self.job["project"], job_id=self.job["id"], step_index=self.step_index)


def input_observations() -> List[JsonObject]:
    observations = []
    for path in sorted((FLEET_HOME / "input-observations").glob("*.json")):
        observations.extend({**{key: value for key, value in record.items()
                                if key not in ("raw_request", "raw_resume", "denied_by")},
                             "request": {**input_request(record["raw_request"]),
                                         "denied_by": record.get("denied_by", [])}}
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
    request = {"tool": raw.get("tool_name") or "a tool",
               "description": description if isinstance(description, str) else "",
               "detail": detail[:2000], "rules": permission_rules(raw.get("tool_name"), tool_input)}
    if raw.get("tool_name") == QUESTION_TOOL:
        request["questions"] = user_questions(tool_input)
        request["detail"] = "\n".join(question["question"] for question in request["questions"])[:2000]
        request["rules"] = []
    return request


def user_questions(tool_input: JsonObject) -> List[JsonObject]:
    """AskUserQuestion's questions: header, question, options (label, description) and multi-select."""
    text = lambda value: value if isinstance(value, str) else ""
    questions = tool_input.get("questions")
    return [{"header": text(question.get("header")), "question": text(question.get("question")),
             "multi_select": question.get("multiSelect") is True,
             "options": [{"label": text(option.get("label")), "description": text(option.get("description"))}
                         for option in question.get("options") or [] if isinstance(option, dict)]}
            for question in questions if isinstance(question, dict)] if isinstance(questions, list) else []


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


def bash_commands(command: str) -> List[List[str]]:
    """The words of each simple command in a compound one, without leading VAR=value or redirects."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    commands: List[List[str]] = []
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
            if words:
                commands.append(words)
        else:
            segment.append(token)
    return commands


def bash_rules(command: str) -> List[str]:
    """One `Bash(prefix:*)` per simple command, as Claude checks each part of a compound command."""
    rules: List[str] = []
    for words in bash_commands(command):
        prefix = words[:2] if words[0] in SUBCOMMAND_PROGRAMS and len(words) > 1 and not words[1].startswith("-") \
            else words[:1]
        rule = f"Bash({' '.join(prefix)}:*)"
        if rule not in rules:
            rules.append(rule)
    return rules


def claude_settings_files(cwd: Optional[str]) -> List[Path]:
    """The settings files whose deny rules apply to a Claude run in cwd: managed, user and project."""
    user = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    files = [Path("/etc/claude-code/managed-settings.json"), user / "settings.json"]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.local.json"]
    return files


def deny_rules_matching(tool: Optional[str], tool_input: JsonObject, cwd: Optional[str]) -> List[str]:
    """The deny rules that refuse this request, with their files. No allow rule can override one.

    Covers bare tool names, Bash commands and prefixes, and rules equal to one fleetd would
    propose; path globs and other patterns are not interpreted, so an empty list is not proof.
    """
    if not tool:
        return []
    proposed = set(permission_rules(tool, tool_input))
    command = tool_input.get("command") if tool == "Bash" else None
    commands = [" ".join(words) for words in bash_commands(command)] if isinstance(command, str) else []
    found = []
    for path in claude_settings_files(cwd):
        try:
            deny = json.loads(path.read_text()).get("permissions", {}).get("deny", [])
        except (OSError, ValueError, AttributeError):
            continue
        for rule in deny if isinstance(deny, list) else []:
            match = re.fullmatch(r"([^()]+)(?:\((.*)\))?", rule) if isinstance(rule, str) else None
            if match is None or match.group(1) != tool:
                continue
            content = match.group(2)
            if content is None or rule in proposed:
                hit = True
            elif commands and content.endswith(":*"):
                prefix = content[:-2]
                hit = any(each == prefix or each.startswith(prefix + " ") for each in commands)
            else:
                hit = content in commands or content == command
            if hit:
                found.append(f"{rule} in {path}")
    return found


def command_input_hook(arguments: argparse.Namespace) -> None:
    record = json.load(sys.stdin)
    if arguments.session_hook:
        # Installed for every session on the host; a job's own --settings hook records its events.
        if os.environ.get("FLEET_JOB_ID") or not isinstance(record.get("cwd"), str):
            return
        project = repository_name(record["cwd"])
    elif arguments.project is None:
        fail("input-hook needs --project, or --session-hook")
    else:
        project = arguments.project
    record_input_hook(record, project=project, job_id=arguments.job, step_index=arguments.step_index)


SESSION_HOOK_MARK = "# fleet-session-hook"   # identifies the entries `session-hooks uninstall` removes


def session_hook_settings() -> JsonObject:
    """The hook groups every interactive Claude session on this host runs.

    The command does nothing, successfully and silently, when this fleetd or its FLEET_HOME is gone,
    so a removed install can never block or clutter a session.
    """
    fleetd, home = shlex.quote(str(Path(__file__).resolve())), shlex.quote(str(FLEET_HOME))
    command = (f"test -d {home} && test -f {fleetd} && FLEET_HOME={home} {shlex.quote(sys.executable)} {fleetd} "
               f"input-hook --session-hook >/dev/null 2>&1; exit 0 {SESSION_HOOK_MARK}")
    group = {"hooks": [{"type": "command", "command": command}]}
    return {"PermissionRequest": [group], "PreToolUse": [{"matcher": QUESTION_TOOL, **group}],
            "PostToolUse": [group]}


def without_session_hooks(hooks: JsonObject) -> JsonObject:
    """The hooks with fleet's marked commands taken out, and any group or event they leave empty."""
    result = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            result[event] = groups
            continue
        kept = []
        for group in groups:
            commands = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(commands, list):
                kept.append(group)
                continue
            remaining = [hook for hook in commands if not (isinstance(hook, dict)
                         and SESSION_HOOK_MARK in str(hook.get("command", "")))]
            if remaining or not commands:
                kept.append({**group, "hooks": remaining} if len(remaining) != len(commands) else group)
        if kept or not groups:
            result[event] = kept
    return result


def command_session_hooks(arguments: argparse.Namespace) -> None:
    """Merge fleet's hooks into (or take them out of) Claude's user settings, keeping everything else."""
    path = Path(arguments.settings or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
                / "settings.json").expanduser()
    try:
        settings = json.loads(path.read_text()) if path.exists() else {}
    except ValueError as error:
        fail(f"{path} is not valid JSON, left unchanged: {error}")
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
        fail(f"{path} does not hold a settings object with a hooks object, left unchanged")
    hooks = without_session_hooks(settings.get("hooks", {}))
    if arguments.action == "install":
        for event, groups in session_hook_settings().items():
            hooks[event] = hooks.get(event, []) + groups
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    path = path.resolve()   # a settings file kept in dotfiles stays a symlink to it
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o600
    temporary = path.with_name(f".{path.name}.fleet-{os.getpid()}.tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as handle:
        handle.write(json.dumps(settings, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    emit({"host": os.uname().nodename, "settings": str(path), "action": arguments.action,
          "events": sorted(event for event, groups in settings.get("hooks", {}).items()
                           if any(SESSION_HOOK_MARK in json.dumps(group) for group in groups))})


def make_step(index: int, prompt: str, title: Optional[str], work_item: Optional[str] = None) -> JsonObject:
    """A pending step; `work_item` names the work the step serves when it is not the job's own."""
    step = {"index": index, "title": title or shorten(prompt.splitlines()[0] if prompt.strip() else prompt, 80),
            "prompt": prompt, "status": "pending", "started_at": None, "finished_at": None, "result": None}
    if work_item is not None:
        step["work_item"] = work_item
    return step


def parse_steps(steps_json: str) -> List[JsonObject]:
    raw_steps = json.loads(steps_json)
    steps = [{"prompt": item, "title": None} if isinstance(item, str) else item for item in raw_steps]
    for step in steps:
        if "work_item" in step and (not isinstance(step["work_item"], str) or not step["work_item"].strip()):
            fail("a step's work_item must be a work item ID")
    return steps


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
    steps = [make_step(index, item["prompt"], item.get("title"), item.get("work_item"))
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
    job["workspace"], job["workspace_reason"] = collect_workspace(cwd)
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
            write_briefs(job_id, steps)
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
                # The job holds on a step waiting for its answer; a delivered reply is that answer.
                for waiting in job["steps"]:
                    if waiting["status"] == "blocked" and waiting.get("answered_by") is None:
                        waiting["answered_by"] = step["index"]
                job["steps"].append(step)
                write_briefs(arguments.job, [step])
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
                             f"Continue step {arguments.step + 1}", job["steps"][arguments.step].get("work_item"))
            job["steps"].append(step)
            job["cancelled"] = False
            grant = {"key": arguments.key, "step": arguments.step, "rules": rules, "added": added,
                     "continuation": step["index"], "at": now()}
            job.setdefault("permission_grants", []).append(grant)
            fresh = True
        else:
            if (grant['step'], grant['rules']) != (arguments.step, rules):
                fail('grant key has changed payload')
            fresh = False
    if fresh:
        append_event(arguments.job, {"kind": "job", "status": "queued",
                                     "summary": f"allowed {', '.join(rules)}; step {arguments.step + 1} continues"})
        launch_runner(arguments.job)
    emit({"schema_version": 1, "key": arguments.key, "status": "applied", "added": grant["added"],
          "continuation": grant["continuation"]})


DECISION_FIELDS = ("id", "work_item", "question", "answer", "principle", "actor", "context")


def command_receive_decision(arguments: argparse.Namespace) -> None:
    if arguments.schema_version != 1:
        fail("unsupported decision schema version")
    decision = json.loads(sys.stdin.read())
    if not isinstance(decision, dict) or not all(isinstance(decision.get(k), str) and decision[k].strip()
                                              for k in ("id", "question", "answer", "actor")):
        fail("decision needs id, question, answer and actor")
    if "principle" not in decision or not (decision["principle"] is None or isinstance(decision["principle"], str)):
        fail("decision needs principle (null when unknown)")
    with locked_job(arguments.job) as job:
        inbox = job.setdefault("decisions_since_dispatch", [])
        held = next((d for d in inbox if d["id"] == decision["id"]), None)
        if held is not None and held != decision:
            fail("decision id has changed payload")
        if held is None:
            inbox.append(decision)
    emit({"schema_version": 1, "key": arguments.key, "status": "applied"})


def command_decision(arguments: argparse.Namespace) -> None:
    """Keep a decision an agent recorded in this job (JSON object on stdin) for the controller to take from the stream.

    The job's decisions are append-only and keyed by the id the agent's CLI generated: a re-sent decision is
    reported as held once more and changes nothing; the same id with a different decision is refused.
    """
    if arguments.schema_version != 1:
        fail("unsupported decision schema version")
    decision = json.loads(sys.stdin.read() or "null")
    if (not isinstance(decision, dict) or not all(isinstance(decision.get(name), str) for name in DECISION_FIELDS)
            or not isinstance(decision.get("time"), (int, float))):
        fail(f"a decision needs {', '.join(DECISION_FIELDS)} as text and time as epoch seconds")
    if not all(decision[name].strip() for name in DECISION_FIELDS if name != "context"):
        fail(f"a decision's {', '.join(name for name in DECISION_FIELDS if name != 'context')} must not be blank")
    decision = {name: decision[name] for name in (*DECISION_FIELDS, "time")}
    with locked_job(arguments.job) as job:
        held = next((held for held in job.get("decisions", []) if held["id"] == decision["id"]), None)
        if held is None:
            job.setdefault("decisions", []).append(decision)
        elif held != decision:
            fail("decision id has changed payload")
    if held is None:
        append_event(arguments.job, {"kind": "job", "status": "decision",
                                     "summary": f"decision recorded: {shorten(decision['question'])}"})
    emit({"schema_version": 1, "id": decision["id"], "status": "held"})


def command_add(arguments: argparse.Namespace) -> None:
    if arguments.key is not None:
        add_keyed(arguments)
        return
    if arguments.answers is not None:
        fail("--answers needs --key")
    new_steps = parse_steps(Path(arguments.steps_file).read_text())
    with locked_job(arguments.job) as job:
        job["cancelled"] = False
        for item in new_steps:
            job["steps"].append(make_step(len(job["steps"]), item["prompt"], item.get("title"), item.get("work_item")))
        if arguments.retry:
            for step in job["steps"]:
                if step["status"] in ("failed", "blocked", "cancelled"):
                    step["status"] = "pending"
                    step.pop("reason", None)
        write_briefs(arguments.job, job["steps"])
    append_event(arguments.job, {"kind": "job", "status": "queued", "summary": f"{len(new_steps)} step(s) added"})
    if not arguments.hold:
        launch_runner(arguments.job)
    emit(job_summary(read_job(arguments.job), 0))


def add_keyed(arguments: argparse.Namespace) -> None:
    """Append steps once per key; with --answers they answer that blocked step, which then no longer blocks the job.

    Idempotent by key: a repeated add reports the first result and queues nothing more.
    """
    if arguments.schema_version != 1:
        fail("unsupported add schema version")
    new_steps = parse_steps(Path(arguments.steps_file).read_text())
    if not arguments.key.strip() or not new_steps or arguments.retry:
        fail("a keyed add needs a key and at least one step, and cannot retry")
    with locked_job(arguments.job) as job:
        added = next((added for added in job.get("keyed_additions", []) if added["key"] == arguments.key), None)
        if added is None:
            if arguments.answers is not None:
                if not 0 <= arguments.answers < len(job["steps"]):
                    fail(f"job has no step {arguments.answers}")
                answered = job["steps"][arguments.answers]
                if answered["status"] != "blocked" or answered.get("answered_by") is not None:
                    fail(f"step {arguments.answers + 1} is not waiting for an answer")
                answered["answered_by"] = len(job["steps"])
                # A reply carries on the answered step's work unless it names its own.
                new_steps = [{"work_item": answered["work_item"], **item} if "work_item" in answered else item
                             for item in new_steps]
            job["cancelled"] = False
            indices = []
            for item in new_steps:
                job["steps"].append(make_step(len(job["steps"]), item["prompt"], item.get("title"), item.get("work_item")))
                indices.append(len(job["steps"]) - 1)
            added = {"key": arguments.key, "answers": arguments.answers, "steps": indices, "at": now()}
            job.setdefault("keyed_additions", []).append(added)
            write_briefs(arguments.job, job["steps"])
            fresh = True
        else:
            if added['answers'] != arguments.answers or len(added['steps']) != len(new_steps):
                fail('add key has changed payload')
            inherited = (job['steps'][arguments.answers].get('work_item')
                         if arguments.answers is not None else None)
            for index, requested in zip(added['steps'], new_steps):
                expected = make_step(index, requested['prompt'], requested.get('title'),
                                     requested.get('work_item', inherited))
                if any(job['steps'][index].get(name) != expected.get(name)
                       for name in ('prompt', 'title', 'work_item')):
                    fail('add key has changed payload')
            fresh = False
    if fresh:
        summary = f"{len(added['steps'])} step(s) added" + (
            f" answering step {added['answers'] + 1}" if added["answers"] is not None else "")
        append_event(arguments.job, {"kind": "job", "status": "queued", "summary": summary})
        if not arguments.hold:
            launch_runner(arguments.job)
    emit({"schema_version": 1, "key": arguments.key, "status": "applied", "answers": added["answers"],
          "steps": added["steps"]})


def command_start(arguments: argparse.Namespace) -> None:
    start_job(arguments.job, arguments)
    emit(job_summary(read_job(arguments.job), 0))


def command_list(arguments: argparse.Namespace) -> None:
    jobs = all_jobs()
    if not arguments.all:
        horizon = now() - arguments.since_hours * 3600
        jobs = [job for job in jobs if derive_status(job) not in AGED_STATUSES or job.get("updated_at", 0) >= horizon]
    emit({"host": os.uname().nodename, "time": now(),
          "jobs": [job_summary(job, arguments.events) for job in jobs]})


def job_signature(directory: Path, documents: bool = True) -> tuple:
    """Changes whenever the job definition, its activity or (with documents) one of its documents changes.

    Document folders are stat-ed, never read. Without documents only the outbox folder itself is
    stat-ed, which is enough to wake a long-finished job the stream no longer follows.
    """
    signature: List[Any] = []
    for name in ("job.json", "events.jsonl") if documents else ("job.json", "events.jsonl", "outbox"):
        with contextlib.suppress(OSError):
            stat = (directory / name).stat()
            signature += [stat.st_mtime_ns, stat.st_size]
    if documents:
        signature += [tree_signature(directory / name) for name in ("outbox", "context", "artifacts")]
    return tuple(signature)


def tree_signature(root: Path) -> tuple:
    """(file count, newest mtime, total size) of the regular files under root."""
    count = newest = total = 0
    pending = [root]
    while pending:
        for entry in scan_directory(pending.pop()):
            with contextlib.suppress(OSError):
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    count, newest, total = count + 1, max(newest, stat.st_mtime_ns), total + stat.st_size
    return count, newest, total


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
    removals: Dict[Path, int] = {}
    try:
        emit({"type": "hello", "host": os.uname().nodename, "time": now(),
              "protocol_version": STREAM_PROTOCOL_VERSION,
              "wire_protocol_version": WIRE_PROTOCOL_VERSION, "worker_version": WORKER_VERSION})
        while True:
            for observation in input_observations():
                occurrence = observation["source_event_id"]
                if inputs.get(occurrence) != observation:
                    emit(observation)
                    inputs[occurrence] = observation
            seen = set()
            for path in JOBS_DIRECTORY.glob("*/job.json") if JOBS_DIRECTORY.exists() else []:
                job_id = path.parent.name
                if job_id in ignored and ignored[job_id] == job_signature(path.parent, documents=False):
                    continue
                signature = job_signature(path.parent)
                # Recheck processes until both runner and agent have ended.
                if signature == signatures.get(job_id) and not runner_states.get(job_id):
                    seen.add(job_id)
                    continue
                try:
                    job = json.loads(path.read_text())
                except (ValueError, OSError):
                    continue
                status = derive_status(job)
                if status in AGED_STATUSES and job.get("updated_at", 0) < now() - horizon_seconds:
                    ignored[job_id] = job_signature(path.parent, documents=False)
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
                removal = REMOVALS_DIRECTORY / f"{hashlib.sha256(job_id.encode()).hexdigest()}.json"
                details = json.loads(removal.read_text()) if removal.is_file() else {"reason": "aged" if (JOBS_DIRECTORY / job_id).exists() else "deleted"}
                emit({"type": "removed", "id": job_id, **details})
            for message in removal_messages(removals):
                emit(message)
            if now() - last_session_scan >= arguments.session_interval:
                last_session_scan = now()
                current = tracker.scan()
                for session_id, session in current.items():
                    if sessions.get(session_id) != session:
                        emit({"type": "session", "session": session})
                for session_id in set(sessions) - set(current):
                    emit({"type": "session_removed", "id": session_id, "updated_at": sessions[session_id]["updated_at"]})
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
    since = parse_timestamp(arguments.since) if getattr(arguments, "since", None) is not None else None
    if getattr(arguments, "since", None) is not None and since is None:
        fail("sessions --since requires an ISO timestamp")
    sessions = SessionTracker(since).scan().values()
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


def trace_summary(job_id: str) -> JsonObject:
    directory = JOBS_DIRECTORY / job_id
    path = directory / "events.jsonl"
    try:
        stat = path.stat() if path.is_file() else None
    except FileNotFoundError:
        stat = None
    return {"path": str(path), "availability": "available" if stat else "unavailable",
            "size": stat.st_size if stat else None, "mtime": stat.st_mtime_ns if stat else None,
            "raw": [{"path": str(raw), "availability": "available"} for raw in sorted(directory.glob("raw-*.jsonl"))]}


def command_read_trace(arguments: argparse.Namespace) -> None:
    """Read the complete normalized trace; no caller-supplied filesystem path."""
    path = job_directory(arguments.job) / "events.jsonl"
    if path.parent.resolve().parent != JOBS_DIRECTORY.resolve():
        fail("trace path outside jobs directory")
    if not path.is_file():
        emit({"content": None, "reason": "worker events.jsonl is missing"})
        return
    if path.is_symlink():
        fail("trace symlinks are refused")
    emit({"content": path.read_bytes().decode("utf-8"), "reason": None})


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
    if not (JOBS_DIRECTORY / arguments.job / "job.json").exists():
        sessions = [identity for identity in SessionTracker().scan() if identity.startswith(arguments.job)]
        if len(sessions) == 1:
            moved = session_projects()
            moved[sessions[0]] = arguments.project
            FLEET_HOME.mkdir(parents=True, exist_ok=True)
            temporary_path = SESSION_PROJECTS_PATH.with_suffix(".tmp")
            temporary_path.write_text(json.dumps(moved, indent=1))
            temporary_path.replace(SESSION_PROJECTS_PATH)
            emit({"id": sessions[0], "project": arguments.project, "session": True})
            return
        if sessions:
            fail(f"'{arguments.job}' matches {len(sessions)} sessions; use more of the id")
    with locked_job(arguments.job) as job:
        job["project"] = arguments.project
    emit(job_summary(read_job(arguments.job), 0))


def removal_messages(seen: Dict[Path, int]) -> Iterator[JsonObject]:
    """Replay confirmed removals after reconnect, including manifests published after job disappearance."""
    for entry in scan_directory(REMOVALS_DIRECTORY):
        if not entry.name.endswith(".json") or not entry.is_file() or entry.is_symlink():
            continue
        path = Path(entry.path)
        modified = entry.stat().st_mtime_ns
        if seen.get(path) == modified:
            continue
        details = json.loads(path.read_text())
        seen[path] = modified
        yield {"type": "removed", **details}


def command_remove(arguments: argparse.Namespace) -> None:
    job = read_job(arguments.job)
    status = derive_status(job)
    if status == "running":
        fail("job is running; cancel it first")
    if status not in REMOVABLE_STATUSES and not arguments.force:
        fail(f"job is {status}, not finished; removing it deletes its outbox and results for good "
             f"(rerun with --force to remove it anyway)")
    directory = JOBS_DIRECTORY / arguments.job
    outbox = directory / "outbox"
    outbox_files = sum(1 for path in outbox.rglob("*") if path.is_file()) if outbox.is_dir() else 0
    results = len(list(directory.glob("result-*.md")))
    shutil.rmtree(directory)
    details = {"id": arguments.job, "reason": "removed by fleet rm", "removed_at": now()}
    REMOVALS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = REMOVALS_DIRECTORY / f"{hashlib.sha256(arguments.job.encode()).hexdigest()}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(details))
    temporary.replace(path)
    emit({"removed": arguments.job, "status": status, "outbox_files": outbox_files, "results": results, **details})


def executable_path(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and os.path.isfile(value) and os.access(value, os.X_OK)


def command_configure(arguments: argparse.Namespace) -> None:
    FLEET_HOME.mkdir(parents=True, exist_ok=True)
    JOBS_DIRECTORY.mkdir(exist_ok=True)
    config = load_config()
    incoming = json.loads(arguments.json)
    overrides = incoming.pop("runtime_overrides", {})
    for name in ("claude", "codex"):
        guess = incoming.pop(name, None)
        if name in overrides:
            selected = str(Path(overrides[name]).expanduser())
            if not executable_path(selected):
                fail(f"{name} runtime binary is not executable: {selected}; configuration unchanged")
            config[name] = selected
        elif executable_path(config.get(name)):
            pass  # An executable configured path wins over every discovery guess.
        elif executable_path(guess):
            config[name] = guess
        else:
            config.pop(name, None)
    config.update(incoming)
    directories = [os.path.dirname(config[name]) for name in ("claude", "codex") if config.get(name)]
    config["path"] = ":".join(dict.fromkeys(directories + [entry for entry in config.get("path", "").split(":") if entry]))
    CONFIG_PATH.write_text(json.dumps(config, indent=1))
    missing = [name for name in ("claude", "codex") if not config.get(name)]
    emit({"host": os.uname().nodename, "config": config, "missing": missing,
          "tmux": shutil.which("tmux") is not None})


def command_version(arguments: Any) -> None:
    emit({"worker_version": WORKER_VERSION, "wire_protocol_version": WIRE_PROTOCOL_VERSION,
          "stream_protocol_version": STREAM_PROTOCOL_VERSION,
          "dispatch_schema_version": DISPATCH_SCHEMA_VERSION})


def main() -> None:
    parser = argparse.ArgumentParser(prog="fleetd")
    commands = parser.add_subparsers(dest="command", required=True)

    version = commands.add_parser("version", help="worker and protocol versions")
    version.set_defaults(handler=command_version)

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

    receive = commands.add_parser("receive-decision", help="receive a controller decision for the next step")
    receive.add_argument("job")
    receive.add_argument("--schema-version", type=int, required=True)
    receive.add_argument("--key", required=True)
    receive.set_defaults(handler=command_receive_decision)
    decision = commands.add_parser("decision", help="hold an agent's decision (JSON object on stdin) on its job")
    decision.add_argument("job")
    decision.add_argument("--schema-version", type=int, required=True)
    decision.set_defaults(handler=command_decision)

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
    add.add_argument("--key", help="add these steps once: a repeat with the same key queues nothing more")
    add.add_argument("--answers", type=int, help="the blocked step these steps answer (needs --key)")
    add.add_argument("--schema-version", type=int, help="required with --key")
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
    sessions.add_argument("--since", help="catch up transcripts modified since an ISO timestamp, capped at 30 days")
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

    read_trace = commands.add_parser("read-trace", help="complete normalized events for retention")
    read_trace.add_argument("job")
    read_trace.set_defaults(handler=command_read_trace)

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

    read_asset = commands.add_parser("read-asset")
    read_asset.add_argument("job")
    read_asset.add_argument("document")
    read_asset.add_argument("path")
    read_asset.set_defaults(handler=command_read_asset)

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
    remove.add_argument("--force", action="store_true")
    remove.set_defaults(handler=command_remove)

    configure = commands.add_parser("configure")
    configure.add_argument("json")
    configure.set_defaults(handler=command_configure)

    run = commands.add_parser("_run")
    run.add_argument("job")
    run.set_defaults(handler=lambda arguments: run_job(arguments.job))

    hook = commands.add_parser("input-hook", help="receive Claude permission hooks on stdin")
    hook.add_argument("--project")
    hook.add_argument("--session-hook", action="store_true",
                      help="installed for every session: project from the hook's cwd; ignored inside fleet jobs")
    hook.add_argument("--job")
    hook.add_argument("--step-index", type=int)
    hook.set_defaults(handler=command_input_hook)

    session_hooks = commands.add_parser("session-hooks",
                                        help="add fleet's hooks to (or remove them from) Claude's user settings")
    session_hooks.add_argument("action", choices=("install", "uninstall"))
    session_hooks.add_argument("--settings", help="settings file (default: ~/.claude/settings.json)")
    session_hooks.set_defaults(handler=command_session_hooks)

    settings = commands.add_parser("input-hook-settings", help="Claude --settings JSON for an interactive session")
    settings.add_argument("--project", required=True)
    settings.set_defaults(handler=lambda args: emit(input_hook_settings(args.project)))

    arguments = parser.parse_args()
    if getattr(arguments, "job", None) and arguments.handler is not command_start:
        arguments.job = expand_job_id(arguments.job)
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
