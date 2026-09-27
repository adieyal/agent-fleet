"""Inspect explicitly named local evidence; never execute it."""

import json
from pathlib import Path

from fleet.modules.work import Evidence


class FileEvidenceReader:
    def get(self, reference: str) -> Evidence | None:
        path = Path(reference)
        if not path.is_absolute() or not path.is_file():
            return None
        result = None
        if path.suffix == ".json":
            content = json.loads(path.read_text())
            if isinstance(content, dict):
                result = content.get("result")
        return Evidence(reference, result)
