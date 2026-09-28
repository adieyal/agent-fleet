"""The recorded fleet with real-shaped libraries behind it, for the project overview's tests.

Restoke's library is tests/fixtures/restoke-library/ralph, cut down from the Restoke V2 Ralph library: a done slice
(v2-suppliers), a slice in progress (slice 4, 14 of 15 stories), a done slice with open questions (slice 5),
a draft slice with 43 unanswered questions (slice 6), a review with REVIEW.md and no README or PRD (v2-review) and a loose
file. agent-fleet's library is a repository with docs/ and no PRD. Jobs gain step briefs, so they can link to
workstreams: b7d042's brief names slice 4's folder, and d4f7a2 runs in the v2-suppliers worktree.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"
RALPH = FIXTURES / "restoke-library" / "ralph"
AGENT_FLEET = "p-a9e1f1ee"
BRIEFS = {
    ("home", "b7d042"): "Run the slice 4 loop from /home/adi/Development/restoke/webapp/ralph/v2-suppliers-slice4 "
                        "and fix what the smoke test finds.",
    ("worker", "d4f7a2"): "Write the PAR-by-weekday support article.",
    ("worker", "f20a6d"): "Rebuild the deck scene.",
}


def repository(root: Path) -> Path:
    """A repository-shaped library: Markdown at the root and under docs/, no PRD."""
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "design").mkdir()
    (root / "README.md").write_text("# agent-fleet\n\nA fleet of coding agents.\n")
    (root / "docs" / "adr" / "0001-sqlite-store.md").write_text("# ADR 1: one SQLite store\n")
    (root / "docs" / "adr" / "0005-storehouse.md").write_text("# ADR 5: the storehouse\n")
    (root / "docs" / "design" / "workspace-prd.md").write_text("# Workspace PRD\n")
    (root / "notes.txt").write_text("not a document")
    (root / "CLAUDE.local.md").write_text("# Private working notes\n")   # someone's local notes: never listed
    return root


def overview_fixture(agent_fleet_root: Path) -> dict[str, Any]:
    """Restoke's library is copied beside `agent_fleet_root`, every file last changed two days before the fixture's time."""
    fixture = json.loads((FIXTURES / "restoke.json").read_text())
    fixture = copy.deepcopy(fixture)
    fixture["projects"][AGENT_FLEET] = {"name": "agent-fleet", "repositories": [],
                                        "links": [{"host": "worker", "label": "agent-fleet"}]}
    ralph = shutil.copytree(RALPH, agent_fleet_root.parent / "ralph")
    two_days_ago = fixture["time"] - 2 * 86400
    for path in ralph.rglob("*"):
        os.utime(path, (two_days_ago, two_days_ago))
    # Restoke's library is configured as it is on carbon: recursive, at the ralph folder
    fixture["library_roots"] = {"restoke": {"path": str(ralph), "recursive": True}, "agent-fleet": str(agent_fleet_root)}
    for host in fixture["hosts"]:
        for job in host["jobs"]:
            brief = BRIEFS.get((host["name"], job["id"]))
            if brief is None:
                continue
            if job["id"] == "d4f7a2":
                job["cwd"] = "/home/adi/Development/restoke/webapp/worktrees/v2-ralph"
            job.setdefault("documents", []).insert(0, {
                "id": "brief-0", "kind": "brief", "name": "Step 1 brief", "step": 0, "size": len(brief),
                "path": f"/home/adi/.fleet/jobs/{job['id']}/brief-0.md", "mtime": job["created_at"]})
            fixture["job_documents"][f"{host['name']}/{job['id']}/brief-0"] = brief
    return fixture
