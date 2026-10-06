"""Inspect explicitly named local evidence; never execute it."""

import json
from pathlib import Path

from fleet.modules.work import Evidence


class FileEvidenceReader:
    def check(self, reference: str) -> None:
        filename = reference.partition("#")[0]
        if not Path(filename).is_absolute():
            raise ValueError(f"evidence reference must be an absolute file path, optionally with #story: {reference}")

    def get(self, reference: str) -> Evidence | None:
        filename, fragment, story_id = reference.partition("#")
        path = Path(filename)
        if not path.is_absolute() or not path.is_file():
            if fragment:
                raise ValueError(f"evidence file is missing or not absolute: {filename}")
            return None
        result = None
        if path.suffix == ".json":
            content = json.loads(path.read_text())
            if fragment:
                story = next((story for story in content["userStories"] if story["id"] == story_id), None)
                if story is None:
                    raise ValueError(f"story {story_id} is missing from {filename}")
                if "passes" not in story:
                    raise ValueError(f"passes is missing for {story_id} in {filename}")
                result = f"passes == {json.dumps(story['passes'])}"
                return Evidence(reference, result)
            if isinstance(content, dict):
                result = content.get("result")
        return Evidence(reference, result)
