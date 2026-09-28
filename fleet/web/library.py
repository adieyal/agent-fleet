"""Read Markdown from explicitly configured local project repositories."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fleet.web.documents import (ASSET_READ_LIMIT, IMAGE_TYPES, AssetNotImage, AssetTooLarge,
                                 DocumentAccessDenied, render_markdown)

READ_LIMIT = 2_000_000
SKIPPED_DIRECTORIES = {"node_modules", "worktrees", "__pycache__", "venv"}


def skipped(relative: Path) -> bool:
    """Hidden files and folders, and bulky tool folders, never enter a recursive library."""
    return any(part.startswith(".") for part in relative.parts) or any(
        part in SKIPPED_DIRECTORIES for part in relative.parts[:-1])


class ProjectLibrary:
    def __init__(self, roots: dict[str, str | dict[str, Any]]) -> None:
        """Each root is a path, or {"path": ..., "recursive": true} to read every folder under it."""
        self.roots = {project: Path(entry if isinstance(entry, str) else entry["path"]).expanduser().resolve()
                      for project, entry in roots.items()}
        self.recursive = {project for project, entry in roots.items()
                          if isinstance(entry, dict) and entry.get("recursive")}

    def list(self) -> list[dict[str, Any]]:
        documents = []
        for project, root in sorted(self.roots.items()):
            if not root.is_dir():
                continue
            order, hidden, show_unlisted = self._display(root)
            position = {document_id: index for index, document_id in enumerate(order)}
            if project in self.recursive:
                paths = sorted(path for path in root.rglob("*.md") if not skipped(path.relative_to(root)))
            else:
                paths = sorted(root.glob("*.md"))
                docs_dir = root / "docs"
                if docs_dir.is_dir():
                    paths.extend(sorted(docs_dir.rglob("*.md")))
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
                title = path.stem
                with resolved.open(encoding="utf-8", errors="replace") as handle:
                    for _, line in zip(range(40), handle):
                        if line.startswith("# "):
                            title = line[2:].strip()
                            break
                documents.append({"project": project, "id": document_id,
                                  "name": path.name, "title": title, "kind": "file",
                                  "size": stat.st_size, "mtime": stat.st_mtime})
        return documents

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

    def _document(self, project: str, document_id: str) -> Path | None:
        root = self.roots.get(project)
        if root is None or "\x00" in document_id or not document_id.endswith(".md"):
            return None
        requested = Path(document_id)
        if requested.is_absolute() or ".." in requested.parts:
            return None
        path = (root / requested).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        relative = path.relative_to(root)
        if project in self.recursive:
            if skipped(relative):
                return None
        elif len(relative.parts) != 1 and relative.parts[0] != "docs":
            return None
        elif len(relative.parts) == 1 and relative.name.startswith("."):
            return None
        return path

    def read_asset(self, project: str, document_id: str, asset_path: str) -> tuple[str, bytes] | None:
        """An image a library document links to, resolved beside it; it must stay under the library root
        and out of hidden or skipped folders. None when the document or image doesn't exist."""
        document = self._document(project, document_id)
        if document is None:
            return None
        root = self.roots[project]
        requested = Path(asset_path)
        if "\x00" in asset_path or requested.is_absolute():
            raise DocumentAccessDenied(f"asset path outside the library root: {asset_path}")
        path = (document.parent / requested).resolve()
        if not path.is_relative_to(root) or skipped(path.relative_to(root)):
            raise DocumentAccessDenied(f"asset path outside the library root: {asset_path}")
        content_type = IMAGE_TYPES.get(path.suffix.lower())
        if content_type is None:
            raise AssetNotImage(f"asset is not a supported image type: {asset_path}")
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(ASSET_READ_LIMIT + 1)
        if len(raw) > ASSET_READ_LIMIT:
            raise AssetTooLarge(f"asset larger than {ASSET_READ_LIMIT} bytes: {asset_path}")
        return content_type, raw

    def read(self, project: str, document_id: str) -> dict[str, Any] | None:
        path = self._document(project, document_id)
        if path is None:
            return None
        with path.open("rb") as handle:
            raw = handle.read(READ_LIMIT + 1)
        markdown = raw[:READ_LIMIT].decode(errors="replace")
        stat = path.stat()
        return {"project": project, "id": document_id, "name": path.name,
                "kind": "file", "size": stat.st_size, "mtime": stat.st_mtime,
                "truncated": len(raw) > READ_LIMIT, **render_markdown(markdown)}
