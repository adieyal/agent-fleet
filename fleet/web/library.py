"""Read Markdown (and Ralph PRDs) from explicitly configured local project libraries.

A library root is one of two shapes, and nothing else is guessed:

- Ralph-style: an immediate subfolder holds a prd.json. Every Markdown file and every prd*.json
  under the root is a document (dot folders and node_modules are skipped).
- A repository: Markdown at the root and under docs/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from fleet.web.documents import render_markdown

READ_LIMIT = 2_000_000
MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")
SKIPPED_FOLDERS = {"node_modules", "__pycache__"}


def is_prd(name: str) -> bool:
    return name.startswith("prd") and name.endswith(".json")


def is_document(name: str) -> bool:
    return name.lower().endswith(MARKDOWN_SUFFIXES) or is_prd(name)


def ralph_style(root: Path) -> bool:
    return any((folder / "prd.json").is_file() for folder in scan(root) if folder.is_dir() and not folder.is_symlink())


def scan(folder: Path) -> list[Path]:
    try:
        return sorted(folder.iterdir())
    except OSError:
        return []


def library_paths(root: Path) -> list[Path]:
    """Every document under the root, as the root's shape allows; symlinks are never followed."""
    if ralph_style(root):
        found = []
        for folder, subfolders, files in os.walk(root):
            subfolders[:] = sorted(name for name in subfolders if not name.startswith(".") and name not in SKIPPED_FOLDERS
                                   and not (Path(folder) / name).is_symlink())
            found += [Path(folder) / name for name in sorted(files)
                      if is_document(name) and not (Path(folder) / name).is_symlink()]
        return found
    paths = [path for path in scan(root) if path.suffix == ".md" and path.is_file() and not path.is_symlink()]
    docs = root / "docs"
    if docs.is_dir() and not docs.is_symlink():
        paths += sorted(path for path in docs.rglob("*.md") if not path.is_symlink())
    return paths


def prd_markdown(prd: dict[str, Any], name: str) -> str:
    """A Ralph prd.json as a page: status, objectives, stories with pass marks, open questions, decisions."""
    def items(value: Any) -> list[str]:
        if value is None:
            return []
        return [str(item) for item in value] if isinstance(value, list) else [str(value)]

    stories = prd.get("userStories") or []
    passing = sum(1 for story in stories if story.get("passes"))
    lines = [f"# {name}", ""]
    if prd.get("description"):
        lines += [str(prd["description"]), ""]
    if prd.get("status"):
        lines += [f"**Status.** {prd['status']}", ""]
    where = [f"branch `{prd['branchName']}`" if prd.get("branchName") else "",
             f"off `{prd['baseBranch']}`" if prd.get("baseBranch") else "",
             f"worktree `{prd['worktree']}`" if prd.get("worktree") else ""]
    if any(where):
        lines += ["**Where.** " + " ".join(part for part in where if part), ""]
    for heading, key in (("Objectives", "objectives"),):
        if items(prd.get(key)):
            lines += [f"## {heading}", "", *(f"- {item}" for item in items(prd.get(key))), ""]
    if stories:
        lines += [f"## Stories · {passing} of {len(stories)} passing", ""]
        for story in stories:
            mark = "x" if story.get("passes") else " "
            extra = " (blocked)" if story.get("blocked") else ""
            lines.append(f"- [{mark}] **{story.get('id', '?')}** {story.get('title', '')}{extra}")
        lines.append("")
    for heading, key in (("Open questions", "openQuestions"), ("Decisions", "decisions"), ("Plan", "plan"),
                         ("Out of scope", "outOfScope")):
        if items(prd.get(key)):
            lines += [f"## {heading}", "", *(f"- {item}" for item in items(prd.get(key))), ""]
    return "\n".join(lines)


class ProjectLibrary:
    def __init__(self, roots: dict[str, str]) -> None:
        self.roots = {project: Path(path).expanduser().resolve() for project, path in roots.items()}
        self.titles: dict[Path, tuple[int, int, str]] = {}   # path → (mtime_ns, size, title), so a large library lists fast

    def root(self, project: str) -> Path | None:
        return self.roots.get(project)

    def list(self) -> list[dict[str, Any]]:
        documents = []
        for project, root in sorted(self.roots.items()):
            if not root.is_dir():
                continue
            order, hidden, show_unlisted = self._display(root)
            position = {document_id: index for index, document_id in enumerate(order)}
            paths = library_paths(root)
            paths.sort(key=lambda path: (position.get(path.relative_to(root).as_posix(), len(position)),
                                         path.relative_to(root).as_posix().lower()))
            for path in paths:
                document_id = path.relative_to(root).as_posix()
                if document_id in hidden or (not show_unlisted and document_id not in position):
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    continue
                stat = resolved.stat()
                documents.append({"project": project, "id": document_id, "name": path.name,
                                  "title": self._title(resolved, stat), "kind": "prd" if is_prd(path.name) else "file",
                                  "size": stat.st_size, "mtime": stat.st_mtime})
        return documents

    def _title(self, path: Path, stat: os.stat_result) -> str:
        cached = self.titles.get(path)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        title = f"{path.parent.name}/{path.name}" if is_prd(path.name) else path.stem
        if not is_prd(path.name):
            with path.open(encoding="utf-8", errors="replace") as handle:
                for _, line in zip(range(40), handle):
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
        self.titles[path] = (stat.st_mtime_ns, stat.st_size, title)
        return title

    @staticmethod
    def _display(root: Path) -> tuple[list[str], set[str], bool]:
        manifest = root / ".fleet" / "library.json"
        if not manifest.exists():
            return [], set(), True
        try:
            config = json.loads(manifest.read_text())
            if not isinstance(config, dict):
                raise ValueError("expected a JSON object")
            order = config.get("order", [])
            hidden = config.get("hide", [])
            show_unlisted = config.get("show_unlisted", True)
            if (not isinstance(order, list) or not isinstance(hidden, list)
                    or not all(isinstance(item, str) for item in order + hidden)
                    or not isinstance(show_unlisted, bool)):
                raise ValueError("expected order and hide lists of paths, and a boolean show_unlisted")
        except (OSError, ValueError) as error:
            raise ValueError(f"invalid library manifest {manifest}: {error}") from error
        return order, set(hidden), show_unlisted

    def path(self, project: str, document_id: str) -> Path | None:
        """The document's file, only if the root's shape lists it and no part of the way is a symlink or hidden."""
        root = self.roots.get(project)
        if root is None or "\x00" in document_id:
            return None
        requested = PurePosixPath(document_id)
        if (requested.is_absolute() or ".." in requested.parts or not requested.parts
                or any(part.startswith(".") for part in requested.parts) or not is_document(requested.name)):
            return None
        if not ralph_style(root):
            if requested.suffix != ".md" or (len(requested.parts) != 1 and requested.parts[0] != "docs"):
                return None
        path = root / requested
        current = root
        for part in requested.parts:
            current = current / part
            if current.is_symlink():
                return None
        if not path.resolve().is_relative_to(root) or not path.is_file():
            return None
        return path

    def read(self, project: str, document_id: str) -> dict[str, Any] | None:
        path = self.path(project, document_id)
        if path is None:
            return None
        with path.open("rb") as handle:
            raw = handle.read(READ_LIMIT + 1)
        text = raw[:READ_LIMIT].decode(errors="replace")
        kind = "file"
        if is_prd(path.name):
            try:
                prd = json.loads(text)
            except ValueError as error:
                text = f"# {document_id}\n\nThis prd.json is not valid JSON: {error}"
            else:
                text = prd_markdown(prd, document_id) if isinstance(prd, dict) else f"# {document_id}\n\nNot a PRD object."
            kind = "prd"
        stat = path.stat()
        return {"project": project, "id": document_id, "name": path.name,
                "kind": kind, "size": stat.st_size, "mtime": stat.st_mtime,
                "truncated": len(raw) > READ_LIMIT, **render_markdown(text)}
