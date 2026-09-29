"""A project's overview: its workstreams, what each has done, what comes next, and what needs the user.

Derived from documents only, the same way every time (no model is asked anything):

- A workstream is a library folder with a prd.json or any top-level Markdown file (a Ralph loop, a spike, a
  review), plus the Fleet jobs linked to it: a job whose cwd is the PRD's worktree or ends in the folder's name,
  or whose step briefs mention the folder.
- When the library has no such folders (a repository with docs/, or no library at all), each distinct
  job description is a workstream; nothing else is inferred.
- A running or waiting job linked to no workstream is a workstream of its own, so running work is always under
  Active. Other jobs linked to no workstream are "other work", by week. Documents in no workstream are
  "other documents", by folder.
- "In progress" needs evidence of current activity: a running or waiting job, or a file in the folder changed
  within a day. Partly done work without it is "paused".

Parsed files are cached by mtime and size, so a large library (hundreds of documents) recomputes fast.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable

STATES = ("in progress", "paused", "blocked", "unknown", "planned", "done")   # the order the overview shows them in
RECENT = 24 * 3600   # a file changed this recently is evidence the work is going on
RUNNING = {"running"}
WAITING = {"queued", "pending"}
FINISHED = {"done", "cancelled"}
FAILED = {"failed", "blocked", "stalled"}
QUESTION = re.compile(r"(?m)^(?=\d+\. \*\*)")
ANSWER = re.compile(r"(?m)^\s*(\*\*Answer[:*]|-?\s*Answer\b)")
STATUS_LINE = re.compile(r"^\*\*Status\.?\*\*\s*(.+)$")


class OverviewCache:
    """Parsed library files and job texts, kept while their mtime and size stay the same."""

    def __init__(self) -> None:
        self.files: dict[Path, tuple[tuple[int, int], Any]] = {}
        self.texts: dict[tuple[str, str, Any], str | None] = {}

    def parsed(self, path: Path, parse: Callable[[str], Any]) -> Any:
        try:
            stat = path.stat()
        except OSError:
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self.files.get(path)
        if cached is None or cached[0] != signature:
            try:
                value = parse(path.read_text(encoding="utf-8", errors="replace"))
            except ValueError:
                value = None
            self.files[path] = cached = (signature, value)
        return cached[1]

    def job_text(self, key: str, document: dict[str, Any], read: Callable[[str, str], str | None]) -> str | None:
        identity = (key, document["id"], (document.get("mtime"), document.get("size")))
        if identity not in self.texts:
            self.texts[identity] = read(key, document["id"]) if document.get("stored") else None
        return self.texts[identity]


def readme(text: str) -> dict[str, str | None]:
    lines = text.splitlines()
    heading = next((line[2:].strip() for line in lines[:20] if line.startswith("# ")), None)
    status = next((match.group(1).strip() for line in lines[:12] if (match := STATUS_LINE.match(line.strip()))), None)
    return {"heading": heading, "status": re.sub(r"\*\*|__|`", "", status) if status else None}   # shown as plain text


def questions(text: str) -> dict[str, int]:
    """Numbered bold questions ("1. **Flag.** …") and how many carry an answer. Other layouts count as none."""
    asked = [block for block in QUESTION.split(text) if re.match(r"\d+\. \*\*", block)]
    return {"total": len(asked), "answered": sum(1 for block in asked if ANSWER.search(block))}


def prd(text: str) -> dict[str, Any] | None:
    value = json.loads(text)
    return value if isinstance(value, dict) else None


def first_line(markdown: str | None) -> str | None:
    for line in (markdown or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text and not text.startswith("FLEET_STATUS"):
            return text[:200]
    return None


def first_sentence(text: str | None) -> str | None:
    if not text:
        return None
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return sentence[:160] + ("…" if len(sentence) > 160 else "")


def week_of(timestamp: float | None) -> str:
    if timestamp is None:
        return "Undated"
    day = datetime.fromtimestamp(timestamp).date()
    return "Week of " + (day - timedelta(days=day.weekday())).strftime("%-d %b %Y")


class Overview:
    def __init__(self, cache: OverviewCache | None = None) -> None:
        self.cache = cache or OverviewCache()

    def build(self, *, name: str, project_id: str | None, library: str | None, root: Path | None,
              documents: list[dict[str, Any]], jobs: list[dict[str, Any]],
              read_job: Callable[[str, str], str | None], attention: list[dict[str, Any]],
              now: float | None = None) -> dict[str, Any]:
        """`documents` are the library's (ProjectLibrary.list for `library`), `jobs` the store's (with availability)."""
        now = time.time() if now is None else now
        folders = self.folders(root, documents) if root is not None else []
        linked: dict[str, list[dict[str, Any]]] = {folder["id"]: [] for folder in folders}
        loose_jobs = []
        for job in jobs:
            homes = self.homes(job, folders, root, read_job)
            for folder in homes:
                linked[folder].append(job)
            if not homes:
                loose_jobs.append(job)
        streams = [self.folder_stream(folder, linked[folder["id"]], library, project_id, read_job, attention, now)
                   for folder in folders]
        other_work = []
        if folders:
            # running work is never hidden in other work: each such job is a card of its own
            streams += [self.job_stream(job.get("description") or job["id"], [job], project_id, read_job, attention)
                        for job in loose_jobs if job.get("status") in RUNNING | WAITING]
            weeks: dict[str, list[dict[str, Any]]] = {}
            for job in sorted((job for job in loose_jobs if job.get("status") not in RUNNING | WAITING),
                              key=lambda job: job.get("created_at") or 0, reverse=True):
                weeks.setdefault(week_of(job.get("created_at")), []).append(self.job_line(job))
            other_work = [{"week": week, "jobs": entries} for week, entries in weeks.items()]
        else:
            by_description: dict[str, list[dict[str, Any]]] = {}
            for job in loose_jobs:
                by_description.setdefault(job.get("description") or job["id"], []).append(job)
            streams += [self.job_stream(description, grouped, project_id, read_job, attention)
                        for description, grouped in by_description.items()]
        streams.sort(key=lambda stream: (STATES.index(stream["state"]), -(stream["last_activity"] or 0)))
        in_folder = {folder["id"] for folder in folders}
        other_documents: dict[str, list[dict[str, Any]]] = {}
        for document in documents:
            top = PurePosixPath(document["id"]).parts[0] if "/" in document["id"] else None
            if top not in in_folder:
                other_documents.setdefault(str(PurePosixPath(document["id"]).parent), []).append(
                    {"label": document.get("title") or document["name"], "trace": library_trace(library, document)})
        counts = {state: sum(1 for stream in streams if stream["state"] == state) for state in STATES}
        return {"project_id": project_id, "library": library, "name": name,
                "summary": summary(streams, counts), "counts": counts, "workstreams": streams,
                "other_work": other_work,
                "other_documents": [{"folder": folder, "documents": entries}
                                    for folder, entries in sorted(other_documents.items(),
                                                                  key=lambda item: (item[0] != ".", item[0]))]}

    # ------------------------------------------------------------------ library folders

    def folders(self, root: Path, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_folder: dict[str, list[dict[str, Any]]] = {}
        for document in documents:
            parts = PurePosixPath(document["id"]).parts
            if len(parts) > 1:
                by_folder.setdefault(parts[0], []).append(document)
        found = []
        for folder, inside in sorted(by_folder.items()):
            names = {document["id"] for document in inside}
            # README.md names the workstream; without one, the most recently changed top-level Markdown file does
            markdown = sorted((document for document in inside if len(PurePosixPath(document["id"]).parts) == 2
                               and document["id"].lower().endswith(".md")),
                              key=lambda document: (document["id"] != f"{folder}/README.md", -(document.get("mtime") or 0),
                                                    document["id"]))
            if f"{folder}/prd.json" not in names and not markdown:
                continue
            found.append({"id": folder, "documents": inside,
                          "prd": self.cache.parsed(root / folder / "prd.json", prd) if f"{folder}/prd.json" in names else None,
                          "readme": self.cache.parsed(root / markdown[0]["id"], readme) if markdown else None,
                          "questions": (self.cache.parsed(root / folder / "questions.md", questions)
                                        if f"{folder}/questions.md" in names else None),
                          "path": str(root / folder),
                          "updated": max([document.get("mtime") or 0 for document in inside] + files_changed(root / folder))})
        return found

    def homes(self, job: dict[str, Any], folders: list[dict[str, Any]], root: Path | None,
              read_job: Callable[[str, str], str | None]) -> list[str]:
        """The folders a job belongs to: its cwd is the PRD's worktree or is named as the folder
        (worktrees/spike-one-shell for ralph/spike-one-shell), or a step brief names the folder."""
        cwd = (job.get("cwd") or "").rstrip("/")
        briefs = "\n".join(text for document in job["documents"] if document["kind"] == "brief"
                           and (text := self.cache.job_text(job["key"], document, read_job)))
        homes = []
        for folder in folders:
            worktree = str((folder["prd"] or {}).get("worktree") or "").rstrip("/")
            mentioned = re.search(rf"(?:{re.escape(folder['path'])}|{re.escape(root.name) + '/' if root else ''}"
                                  rf"{re.escape(folder['id'])})(?![\w.-])", briefs) if briefs else None
            if (worktree and cwd == worktree) or PurePosixPath(cwd).name == folder["id"] or mentioned:
                homes.append(folder["id"])
        return homes

    def folder_stream(self, folder: dict[str, Any], jobs: list[dict[str, Any]], library: str | None,
                      project_id: str | None, read_job: Callable[[str, str], str | None],
                      attention: list[dict[str, Any]], now: float) -> dict[str, Any]:
        spec, notes, asked = folder["prd"] or {}, folder["readme"] or {}, folder["questions"]
        stories = spec.get("userStories") if isinstance(spec.get("userStories"), list) else []
        documents = {document["id"]: document for document in folder["documents"]}
        trace = lambda name: library_trace(library, documents[f"{folder['id']}/{name}"])   # noqa: E731
        prd_trace = trace("prd.json") if f"{folder['id']}/prd.json" in documents else None
        done = [{"label": f"{story.get('id', '?')} {story.get('title', '')}".strip(), "trace": prd_trace}
                for story in stories if story.get("passes")]
        upcoming = [story for story in stories if not story.get("passes")]
        upcoming.sort(key=lambda story: (story.get("priority") if isinstance(story.get("priority"), (int, float)) else 1e9))
        next_steps = [{"label": f"{story.get('id', '?')} {story.get('title', '')}".strip()
                       + (" (blocked)" if story.get("blocked") else ""), "trace": prd_trace} for story in upcoming]
        needs = []
        questions_trace = trace("questions.md") if asked is not None else None
        if asked and asked["total"] - asked["answered"] > 0:
            needs.append({"label": f"{asked['total'] - asked['answered']} open question"
                          f"{'' if asked['total'] - asked['answered'] == 1 else 's'} of {asked['total']} in questions.md",
                          "trace": questions_trace})
        job_done, job_next, job_needs = self.job_items(jobs, project_id, read_job, attention)
        traces = [{"label": label, "trace": trace(name)} for label, name in
                  (("README", "README.md"), ("PRD", "prd.json"), ("Questions", "questions.md"))
                  if f"{folder['id']}/{name}" in documents]
        shown = {f"{folder['id']}/{name}" for name in ("README.md", "prd.json", "questions.md")}
        notes_docs = [document for document in folder["documents"] if document["id"].startswith(f"{folder['id']}/notes/")]
        top_docs = [document for document in folder["documents"]
                    if len(PurePosixPath(document["id"]).parts) == 2 and document["id"] not in shown]
        traces += [{"label": document["id"].split("/", 1)[1], "trace": library_trace(library, document)}
                   for document in notes_docs + top_docs]
        traces += self.job_traces(jobs, project_id)
        return {"id": folder["id"], "title": notes.get("heading") or first_sentence(spec.get("description")) or folder["id"],
                "summary": notes.get("status") or (str(spec["status"]) if spec.get("status") else None)
                or first_sentence(spec.get("description")),
                "state": derive_state(stories, asked, jobs, notes.get("status") or str(spec.get("status") or ""),
                                      recent=now - folder["updated"] < RECENT),
                "stories": {"passing": len(done), "total": len(stories)} if stories else None,
                "questions": ({"open": asked["total"] - asked["answered"], "total": asked["total"],
                               "trace": questions_trace} if asked and asked["total"] else None),
                "jobs": [self.job_line(job) for job in jobs],
                # what jobs did or are doing leads: it is the latest news, where stories are the long record
                "done": job_done + done, "next": job_next + next_steps, "needs": needs + job_needs,
                "traces": traces,
                "last_activity": max([folder["updated"]] + [job.get("updated_at") or 0 for job in jobs]) or None}

    # ------------------------------------------------------------------ jobs

    def job_stream(self, description: str, jobs: list[dict[str, Any]], project_id: str | None,
                   read_job: Callable[[str, str], str | None], attention: list[dict[str, Any]]) -> dict[str, Any]:
        done, upcoming, needs = self.job_items(jobs, project_id, read_job, attention)
        return {"id": "job:" + (jobs[0]["key"]), "title": description, "summary": None,
                "state": derive_state([], None, jobs, ""), "stories": None, "questions": None,
                "jobs": [self.job_line(job) for job in jobs], "done": done, "next": upcoming, "needs": needs,
                "traces": self.job_traces(jobs, project_id),
                "last_activity": max(job.get("updated_at") or job.get("created_at") or 0 for job in jobs) or None}

    def job_items(self, jobs: list[dict[str, Any]], project_id: str | None,
                  read_job: Callable[[str, str], str | None], attention: list[dict[str, Any]]) -> tuple[list, list, list]:
        done, upcoming, needs = [], [], []
        for job in sorted(jobs, key=lambda job: job.get("created_at") or 0):
            documents = {document["id"]: document for document in job["documents"]}
            for step in job.get("steps", []):
                report = documents.get(f"report-{step['index']}")
                brief = documents.get(f"brief-{step['index']}")
                line = first_line(self.cache.job_text(job["key"], report, read_job)) if report else None
                label = f"{job.get('description') or job['id']} · step {step['index'] + 1}: {step.get('title') or ''}".strip()
                if step.get("status") == "done":
                    done.append({"label": label + (f" — {line}" if line else ""),
                                 "trace": job_trace(project_id, job, report or brief)})
                elif step.get("status") in RUNNING:
                    upcoming.append({"label": "Running now: " + label, "trace": job_trace(project_id, job, brief)})
                elif step.get("status") in WAITING:
                    upcoming.append({"label": "Queued: " + label, "trace": job_trace(project_id, job, brief)})
                elif step.get("status") in FAILED:
                    needs.append({"label": f"{step['status'].capitalize()}: {label}" +(f" — {line}" if line else ""),
                                  "trace": job_trace(project_id, job, report or brief)})
            owner = f"{job['host']}:{job['id']}"
            needs += [{"label": f"{item['kind']}: {item.get('summary') or ''}".strip(), "trace": None, "attention": item["id"]}
                      for item in attention if (item.get("owner") or {}).get("key") == owner and item.get("state") != "resolved"]
        return done, upcoming, needs

    @staticmethod
    def job_traces(jobs: list[dict[str, Any]], project_id: str | None) -> list[dict[str, Any]]:
        order = {"brief": 0, "report": 1, "file": 2, "outbox": 3, "context": 4}
        traces = []
        for job in sorted(jobs, key=lambda job: job.get("created_at") or 0, reverse=True):
            for document in sorted(job["documents"], key=lambda document: (order.get(document["kind"], 9), document.get("step") or 0)):
                if document.get("stored"):
                    traces.append({"label": f"{document['name']} · {job['host']}:{job['id']}",
                                   "trace": job_trace(project_id, job, document)})
        return traces

    @staticmethod
    def job_line(job: dict[str, Any]) -> dict[str, Any]:
        report = next((document for document in reversed(job["documents"]) if document["kind"] == "report"
                       and document.get("stored")), None)
        return {"key": job["key"], "host": job["host"], "id": job["id"], "description": job.get("description"),
                "status": job.get("status"), "availability": job.get("availability"), "created_at": job.get("created_at"),
                "report": job_trace(job.get("project_id"), job, report)}


def derive_state(stories: list[dict[str, Any]], asked: dict[str, int] | None, jobs: list[dict[str, Any]],
                 status: str, recent: bool = False) -> str:
    """In order: a running or waiting job; every story passing; a blocked story, or questions with none answered;
    some stories passing (in progress if a file changed recently, else paused); none passing. Without stories:
    the README's status words, then the jobs' outcomes, then whether a file changed recently."""
    statuses = [job.get("status") for job in sorted(jobs, key=lambda job: job.get("created_at") or 0)]
    if any(status in RUNNING | WAITING for status in statuses):
        return "in progress"
    if stories:
        if all(story.get("passes") for story in stories):
            return "done"
        if any(story.get("blocked") for story in stories) or (asked and asked["total"] and not asked["answered"]):
            return "blocked"
        if not any(story.get("passes") for story in stories):
            return "planned"
        return "in progress" if recent else "paused"
    if asked and asked["total"] and not asked["answered"]:
        return "blocked"
    words = status.lower()
    if "draft" in words:
        return "planned"
    if statuses:
        latest = statuses[-1]
        return "blocked" if latest in FAILED else "done" if latest in FINISHED else "unknown"
    return "in progress" if recent else "paused"


def files_changed(folder: Path) -> list[float]:
    """The mtimes of the files directly in a folder, listed or not (a loop's progress.txt counts as activity)."""
    try:
        with os.scandir(folder) as entries:
            return [entry.stat().st_mtime for entry in entries if entry.is_file()]
    except OSError:
        return []


def summary(streams: list[dict[str, Any]], counts: dict[str, int]) -> str:
    if not streams:
        return "No workstreams yet: no Ralph folders in the library and no Fleet jobs stored for this project."
    parts = [f"{counts[state]} {state}" for state in STATES if counts[state]]
    text = f"{len(streams)} workstream{'s' if len(streams) != 1 else ''}: {', '.join(parts)}."
    active = [stream["title"] for stream in streams if stream["state"] == "in progress"]
    text += f" Active now: {'; '.join(active)}." if active else " Nothing is active right now."
    waiting = sum(stream["questions"]["open"] for stream in streams if stream["questions"])
    if waiting:
        text += f" {waiting} open question{'s' if waiting != 1 else ''} wait for you."
    return text


def library_trace(library: str | None, document: dict[str, Any]) -> dict[str, Any]:
    return {"source": "library", "project": library, "id": document["id"], "name": document["name"],
            "kind": document.get("kind", "file")}


def job_trace(project_id: str | None, job: dict[str, Any], document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None or not document.get("stored"):
        return None
    return {"source": "job", "project": project_id, "job": job["key"], "id": document["id"], "name": document["name"],
            "kind": document["kind"], "host": job["host"], "job_id": job["id"], "agent": job.get("agent"),
            "description": job.get("description")}
