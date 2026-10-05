import pytest
import json
from pathlib import Path

from fleet.modules.execution import ExecutionFacade


@pytest.mark.parametrize("name,summary,expected", [
    ("Read", "a.py", "read"), ("Edit", "a.py", "edit"), ("Write", "notes.md", "doc"),
    ("apply_patch", "a.md, b.txt", "doc"), ("AskUserQuestion", "Proceed?", "wait"),
    ("Bash", "bash -lc 'cd src && timeout 60 uv run pytest -q'", "test"),
    ("shell", "npm test", "test"), ("Bash", "git commit -m done", "ship"),
    ("Bash", "git diff", "review"), ("Bash", "npm run lint", "test"),
    ("Bash", "ls -la", "search"), ("Bash", "sleep 3", "wait"),
    ("Bash", "npm install", "build"), ("Bash", "curl example.com", "web"),
])
def test_existing_deck_activity_classification(name, summary, expected):
    assert ExecutionFacade.classify_activity(dict(kind="tool", name=name, summary=summary)) == expected


def test_demo_observations_use_the_shared_classification():
    source = (Path(__file__).parents[3] / "packages/fleet-web/src/fleet_web/static/js/demo-events.js").read_text()
    catalog = json.loads(source.split("export const demoEvents = ", 1)[1].rstrip(";\n"))
    for events in catalog.values():
        for event in events:
            assert event["activity_class"] == ExecutionFacade.classify_activity(event)
