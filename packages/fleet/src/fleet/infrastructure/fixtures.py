"""Recorded fixture file and document adapters; no presentation dependencies."""
import json
from pathlib import Path
from typing import Any
from fleet.services.documents import is_private


def load_fixture(path):
    fixture = json.loads(Path(path).read_text())
    fixture['library_roots'] = {project: str(Path(path).parent / entry) if isinstance(entry, str)
                               else {**entry, 'path': str(Path(path).parent / entry['path'])}
                               for project, entry in fixture.get('library_roots', {}).items()}
    return fixture


class FixtureLibraryReader:
    """Same surface as ProjectLibrary, over the fixture's `library` section and any real `library_roots`
    (folders, relative to the fixture file), which are read as a ProjectLibrary reads them."""

    def __init__(self, fixture: dict[str, Any], files) -> None:
        self.projects: dict[str, list[dict[str, Any]]] = fixture.get("library", {})
        self.files = files
        self.roots = {**{project: None for project in self.projects}, **self.files.roots}

    def root(self, project: str) -> Path | None:
        return self.files.root(project)

    def list(self) -> list[dict[str, Any]]:
        return sorted([{"project": project, "id": doc["id"], "name": Path(doc["id"]).name, "title": title(doc),
                        "kind": "file", "size": len(doc["markdown"].encode()), "mtime": doc["mtime"]}
                       for project, docs in self.projects.items() if project not in self.files.roots for doc in docs
                       if not is_private(Path(doc["id"]).name)]
                      + self.files.list(), key=lambda doc: doc["project"])

    def read(self, project: str, document_id: str) -> dict[str, Any] | None:
        if project in self.files.roots:
            return self.files.read(project, document_id)
        doc = next((doc for doc in self.projects.get(project, []) if doc["id"] == document_id), None)
        if doc is None or is_private(Path(document_id).name):
            return None
        return {"project": project, "id": document_id, "name": Path(document_id).name, "kind": "file",
                "size": len(doc["markdown"].encode()), "mtime": doc["mtime"], "truncated": False,
                "content": doc["markdown"]}

    def read_asset(self, project: str, document_id: str, asset_path: str) -> tuple[str, bytes] | None:
        if project in self.files.roots:
            return self.files.read_asset(project, document_id, asset_path)
        return None  # the fixture's own library section is Markdown only


def title(doc: dict[str, Any]) -> str:
    heading = next((line[2:].strip() for line in doc["markdown"].splitlines()[:40] if line.startswith("# ")), None)
    return heading or Path(doc["id"]).stem
