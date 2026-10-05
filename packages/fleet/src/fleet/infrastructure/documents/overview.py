"""Cached file reads for overview queries."""
import os
from pathlib import Path
from typing import Any, Callable

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

def files_changed(folder: Path) -> list[float]:
    """The mtimes of the files directly in a folder, listed or not (a loop's progress.txt counts as activity)."""
    try:
        with os.scandir(folder) as entries:
            return [entry.stat().st_mtime for entry in entries if entry.is_file()]
    except OSError:
        return []
