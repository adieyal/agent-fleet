"""Each project's documents on the machine running fleet web: `~/.fleet/projects/<project id>/`.

`jobs/<host>-<job id>/` holds a copy of every document a job listed (step briefs, step reports,
Markdown the agent wrote, outbox and context Markdown) and `job.json`, a small summary with the
document index. Fleet web fills it as documents appear and change, so they outlive the worktree,
the job directory on the host, `fleet rm` and the agent leaving the floor. `working/` belongs to
the project (a Ralph loop's PRDs and notes): Fleet lists and reads it, never writes there.

The store is not a git repository and sits outside every project repository and worktree. Writes
are serialised: one keeper thread per fleet web, and a lock file per project against a second one.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import re
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from fleet.transport import FleetError
from fleet.web.documents import STATUS_LINE, render_markdown

UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")
READ_LIMIT = 2_000_000
JOB_FIELDS = ("id", "project", "description", "agent", "model", "cwd", "status", "created_at", "updated_at")
STEP_FIELDS = ("index", "title", "status", "started_at", "finished_at")
DOCUMENT_FIELDS = ("id", "kind", "name", "step", "path", "size", "mtime")


def fleet_home() -> Path:
    return Path(os.environ.get("FLEET_HOME") or "~/.fleet").expanduser()


def safe_name(text: str) -> str:
    """A worker-supplied name as a single harmless path component."""
    return (UNSAFE.sub("_", text).strip("._") or "document")[:120]


class ProjectDocuments:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else fleet_home() / "projects"
        self.lock = threading.Lock()

    def project_directory(self, project_id: str) -> Path:
        return self.root / safe_name(project_id)

    def job_directory(self, project_id: str, host: str, job_id: str) -> Path:
        return self.project_directory(project_id) / "jobs" / safe_name(f"{host}-{job_id}")

    @contextlib.contextmanager
    def writing(self, project_id: str) -> Iterator[None]:
        directory = self.project_directory(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        with self.lock, open(directory / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _summary(self, directory: Path) -> dict[str, Any] | None:
        path = directory / "job.json"
        if path.is_symlink() or not path.is_file():
            return None
        try:
            summary = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return summary if isinstance(summary, dict) else None

    @staticmethod
    def _write(path: Path, text: str) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text)
        temporary.replace(path)

    # ------------------------------------------------------------------ filling

    def observe(self, project_id: str, host: str, job: dict[str, Any]) -> list[dict[str, Any]]:
        """Record the job's summary and document index; return the listed documents whose copy is missing or stale."""
        directory = self.job_directory(project_id, host, job["id"])
        with self.writing(project_id):
            directory.mkdir(parents=True, exist_ok=True)
            previous = self._summary(directory) or {}
            documents = copy.deepcopy(previous.get("documents", {}))
            stale = []
            for listed in job.get("documents", []):
                entry = documents.setdefault(listed["id"], {})
                entry.update({key: listed.get(key) for key in DOCUMENT_FIELDS})
                if entry.get("copied") != [listed.get("mtime"), listed.get("size")]:
                    stale.append(listed)
            summary = {**{key: job.get(key) for key in JOB_FIELDS}, "host": host, "project_id": project_id,
                       "steps": [{key: step.get(key) for key in STEP_FIELDS} for step in job.get("steps", [])],
                       "documents": documents}
            if summary != previous:
                self._write(directory / "job.json", json.dumps(summary, indent=1))
        return stale

    def keep(self, project_id: str, host: str, job_id: str, document: dict[str, Any], content: str,
             truncated: bool = False) -> None:
        directory = self.job_directory(project_id, host, job_id)
        with self.writing(project_id):
            summary = self._summary(directory)
            if summary is None or document["id"] not in summary["documents"]:
                return
            entry = summary["documents"][document["id"]]
            if not entry.get("file"):
                taken = {other.get("file") for other in summary["documents"].values()}
                stem = safe_name(document["id"].removesuffix(".md"))
                entry["file"] = next(name for name in (f"{stem}.md", *(f"{stem}-{n}.md" for n in range(2, 10_000)))
                                     if name not in taken and name != "job.json")
            self._write(directory / entry["file"], content)
            entry.update(copied=[document.get("mtime"), document.get("size")], truncated=truncated, error=None)
            self._write(directory / "job.json", json.dumps(summary, indent=1))

    def failed(self, project_id: str, host: str, job_id: str, document_id: str, error: str) -> None:
        """Say why a document has no copy yet; an earlier copy, if any, stays readable."""
        directory = self.job_directory(project_id, host, job_id)
        with self.writing(project_id):
            summary = self._summary(directory)
            if summary is not None and document_id in summary["documents"]:
                summary["documents"][document_id]["error"] = error
                self._write(directory / "job.json", json.dumps(summary, indent=1))

    # ------------------------------------------------------------------ reading

    def projects(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.name for path in self.root.iterdir() if path.is_dir() and not path.is_symlink())

    def jobs(self, project_id: str) -> list[dict[str, Any]]:
        """Every stored job of the project, newest first, each with its documents and whether each has a copy."""
        jobs_directory = self.project_directory(project_id) / "jobs"
        if not jobs_directory.is_dir() or jobs_directory.is_symlink():
            return []
        found = []
        for directory in jobs_directory.iterdir():
            summary = None if directory.is_symlink() else self._summary(directory)
            if summary is None:
                continue
            documents = [{**{key: entry.get(key) for key in DOCUMENT_FIELDS}, "stored": bool(entry.get("file")),
                          "error": entry.get("error")} for entry in summary.get("documents", {}).values()]
            found.append({**{key: value for key, value in summary.items() if key != "documents"},
                          "key": directory.name, "documents": documents})
        return sorted(found, key=lambda job: job.get("created_at") or 0, reverse=True)

    def text(self, project_id: str, job_key: str, document_id: str) -> str | None:
        """A stored document's Markdown as the host served it, or None when there is no copy."""
        found = self._stored(project_id, job_key, document_id)
        return None if found is None else found[2].decode(errors="replace")

    def _stored(self, project_id: str, job_key: str, document_id: str) -> tuple[dict, dict, bytes] | None:
        if job_key != safe_name(job_key):
            return None
        directory = self.project_directory(project_id) / "jobs" / job_key
        summary = None if directory.is_symlink() else self._summary(directory)
        entry = (summary or {}).get("documents", {}).get(document_id)
        if summary is None or entry is None or not entry.get("file"):
            return None
        raw = self._contained(project_id, directory / entry["file"])
        return None if raw is None else (summary, entry, raw)

    def read(self, project_id: str, job_key: str, document_id: str) -> dict[str, Any] | None:
        found = self._stored(project_id, job_key, document_id)
        if found is None:
            return None
        summary, entry, raw = found
        markdown = STATUS_LINE.sub("", raw.decode(errors="replace")).strip()
        return {**{key: entry.get(key) for key in DOCUMENT_FIELDS}, "truncated": bool(entry.get("truncated")),
                "job": summary.get("id"), "host": summary.get("host"), "agent": summary.get("agent"),
                "job_description": summary.get("description"), "project_id": project_id,
                **render_markdown(markdown)}

    def working(self, project_id: str) -> list[dict[str, Any]]:
        """Markdown under the project's own working/ folder; symlinks are skipped."""
        root = self.project_directory(project_id) / "working"
        if not root.is_dir() or root.is_symlink():
            return []
        documents = []
        for folder, subfolders, files in os.walk(root):
            subfolders[:] = sorted(name for name in subfolders if not (Path(folder) / name).is_symlink())
            for name in sorted(files):
                path = Path(folder) / name
                if name.lower().endswith(MARKDOWN_SUFFIXES) and not path.is_symlink() and path.is_file():
                    stat = path.stat()
                    documents.append({"id": path.relative_to(root).as_posix(), "name": name, "kind": "working",
                                      "size": stat.st_size, "mtime": stat.st_mtime})
        return documents

    def read_working(self, project_id: str, document_id: str) -> dict[str, Any] | None:
        requested = PurePosixPath(document_id)
        if (requested.is_absolute() or ".." in requested.parts or "\x00" in document_id
                or not document_id.lower().endswith(MARKDOWN_SUFFIXES)):
            return None
        path = self.project_directory(project_id) / "working" / requested
        raw = self._contained(project_id, path)
        if raw is None:
            return None
        stat = path.stat()
        return {"id": document_id, "name": path.name, "kind": "working", "size": stat.st_size, "mtime": stat.st_mtime,
                "project_id": project_id, "truncated": len(raw) > READ_LIMIT,
                **render_markdown(raw[:READ_LIMIT].decode(errors="replace"))}

    def _contained(self, project_id: str, path: Path) -> bytes | None:
        """The file's bytes, only if it is a regular file (no symlink on the way) inside the project's store."""
        root = self.project_directory(project_id)
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            return None
        current = root
        for part in ("", *parts):
            current = current / part if part else current
            if current.is_symlink():
                return None
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            return None
        with open(path, "rb") as handle:
            return handle.read(READ_LIMIT + 1)


class DocumentKeeper:
    """Copies jobs' documents into the store in the background, so the stream never waits on a read.

    Only the newest listing of each job is kept; unchanged documents (same mtime and size) are skipped.
    A read that fails is recorded on the document and retried the next time the job is listed.
    """

    def __init__(self, store: ProjectDocuments, fetch: Callable[[str, str, str], dict[str, Any]]) -> None:
        self.store, self.fetch = store, fetch
        self.pending: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self.wake = threading.Condition()
        self.busy = False
        self.thread: threading.Thread | None = None

    def observe(self, host: str, project_id: str, job: dict[str, Any]) -> None:
        with self.wake:
            self.pending[(host, job["id"])] = (project_id, job)
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
            self.wake.notify_all()

    def run(self) -> None:
        while True:
            with self.wake:
                self.wake.wait_for(lambda: self.pending)
                (host, _), (project_id, job) = self.pending.popitem()
                self.busy = True
            try:
                self.copy(host, project_id, job)
            finally:
                with self.wake:
                    self.busy = False
                    self.wake.notify_all()

    def copy(self, host: str, project_id: str, job: dict[str, Any]) -> None:
        for document in self.store.observe(project_id, host, job):
            try:
                read = self.fetch(host, job["id"], document["id"])
                self.store.keep(project_id, host, job["id"], document, read["content"], bool(read.get("truncated")))
            except (FleetError, KeyError, OSError) as error:
                self.store.failed(project_id, host, job["id"], document["id"], str(error) or type(error).__name__)

    def settle(self, timeout: float) -> bool:
        """Wait until nothing is pending (tests and shutdown); true when settled."""
        with self.wake:
            return self.wake.wait_for(lambda: not self.pending and not self.busy, timeout=timeout)
