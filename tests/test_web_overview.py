"""A project's overview, derived from its library folders and its stored jobs."""

import json
import os
import shutil
from pathlib import Path

import pytest

from fleet.web.fixture import FixtureLibrary, FixtureState
from fleet.web.library import ProjectLibrary, library_paths
from fleet.web.overview import Overview
from overview_fixture import AGENT_FLEET, RALPH, overview_fixture, repository


def restoke(root: Path = RALPH, **jobs) -> dict:
    library = ProjectLibrary({"restoke": str(root)})
    return Overview().build(name="Restoke", project_id=None, library="restoke", root=library.root("restoke"),
                            documents=library.list(), jobs=[], read_job=lambda *_: None, attention=[])


def by_id(overview: dict) -> dict:
    return {stream["id"]: stream for stream in overview["workstreams"]}


def test_workstreams_come_from_ralph_folders() -> None:
    overview = restoke()
    streams = by_id(overview)
    assert [stream["state"] for stream in overview["workstreams"]] == ["in progress", "blocked", "done", "done"]
    assert {key: stream["state"] for key, stream in streams.items()} == {
        "v2-suppliers": "done", "v2-suppliers-slice4": "in progress", "v2-suppliers-slice5": "done",
        "v2-suppliers-slice6": "blocked"}

    draft = streams["v2-suppliers-slice6"]
    assert draft["title"].startswith("Ralph loop: V2 suppliers, slice 6")
    assert draft["summary"].startswith("prd.json is drafted and has not been reviewed")
    assert draft["stories"] == {"passing": 0, "total": 15}
    assert draft["questions"]["open"] == 43
    assert draft["needs"][0]["label"] == "43 open questions of 43 in questions.md"
    assert draft["needs"][0]["trace"]["id"] == "v2-suppliers-slice6/questions.md"
    assert [item["label"].split()[0] for item in draft["next"]][:3] == ["US-091", "US-092", "US-093"]
    assert draft["done"] == []
    assert [trace["label"] for trace in draft["traces"]] == ["README", "PRD", "Questions", "notes/format.md",
                                                             "notes/legacy-map.md"]

    slice4 = streams["v2-suppliers-slice4"]
    assert slice4["stories"] == {"passing": 14, "total": 15}
    assert [item["label"] for item in slice4["next"]] == ["US-071 Slice 4 browser smoke test and ready parity checklists"]
    assert slice4["needs"] == []   # all 14 questions answered
    assert len(slice4["done"]) == 14 and slice4["done"][0]["trace"]["id"] == "v2-suppliers-slice4/prd.json"

    answered_later = streams["v2-suppliers-slice5"]   # every story passes, yet 28 questions were never answered
    assert answered_later["needs"][0]["label"] == "28 open questions of 30 in questions.md"

    assert "4 workstreams: 1 in progress, 1 blocked, 2 done." in overview["summary"]
    assert "Active now: Ralph loop: V2 suppliers, slice 4" in overview["summary"]
    # a folder with neither README nor PRD is not a workstream; its documents and loose files are listed as they are
    assert [(group["folder"], [doc["trace"]["id"] for doc in group["documents"]]) for group in overview["other_documents"]] == [
        (".", ["legacy-faults-copied-to-v2.md"]), ("v2-review", ["v2-review/findings.md", "v2-review/REVIEW.md"])]


def test_jobs_link_by_worktree_or_brief_and_the_rest_are_other_work(tmp_path: Path) -> None:
    state = FixtureState(overview_fixture(repository(tmp_path / "agent-fleet")))
    projects = {project["name"]: project for project in state.library_overview(FixtureLibrary(state.fixture))}

    restoke_streams = by_id(projects["Restoke"])
    slice4 = restoke_streams["v2-suppliers-slice4"]            # b7d042's brief names the folder, and it is running
    assert [job["id"] for job in slice4["jobs"]] == ["b7d042"]
    assert slice4["state"] == "in progress"
    job_next = [(item["label"], item["trace"]) for item in slice4["next"] if "·" in item["label"]]
    # a step with no brief stored (recorded before briefs existed) says so with no link, rather than borrowing one
    assert job_next == [("Running now: Fix the flaky invoice upload e2e test · step 2: Fix the race in the upload poller", None),
                        ("Queued: Fix the flaky invoice upload e2e test · step 3: Re-run 50× to confirm", None)]
    assert "Step 1 brief · home:b7d042" in [trace["label"] for trace in slice4["traces"]]

    first_slice = restoke_streams["v2-suppliers"]               # d4f7a2 ran in the PRD's worktree
    assert [job["id"] for job in first_slice["jobs"]] == ["d4f7a2"]
    assert first_slice["state"] == "done"
    [finished] = [item for item in first_slice["done"] if "step 2" in item["label"]]
    assert finished["label"] == "Write the PAR-by-weekday support article · step 2: Draft the article — PAR by weekday: draft ready"
    assert (finished["trace"]["source"], finished["trace"]["job"], finished["trace"]["id"]) == ("job", "worker-d4f7a2", "report-1")

    assert [(week["week"], [job["id"] for job in week["jobs"]]) for week in projects["Restoke"]["other_work"]] == [
        ("Week of 21 Sep 2026", ["c90e11", "a1c3e9", "e1b5c8"])]

    # agent-fleet has no Ralph folders: its job is a workstream by description, its docs are listed by folder
    fleet = projects["agent-fleet"]
    assert fleet["project_id"] == AGENT_FLEET
    [stream] = fleet["workstreams"]
    assert (stream["title"], stream["state"]) == ("Split the deck into ES modules", "in progress")
    assert fleet["other_work"] == []
    # a failed job is blocked and says why; a queued one is planned
    invoices = {stream["title"]: stream for stream in projects["Invoice analysis"]["workstreams"]}
    assert invoices["Collect credit-note samples"]["state"] == "blocked"
    assert invoices["Tune LUC tolerance for credit notes"]["state"] == "planned"
    assert [(group["folder"], len(group["documents"])) for group in fleet["other_documents"]] == [
        (".", 1), ("docs/adr", 2), ("docs/design", 1)]


def test_the_overview_follows_document_changes_and_reuses_unchanged_parses(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ralph"
    shutil.copytree(RALPH, root)
    library = ProjectLibrary({"restoke": str(root)})
    overview = Overview()
    parses = []
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: parses.append(self.name) or real_read(self, *a, **k))

    def build():
        return by_id(overview.build(name="Restoke", project_id=None, library="restoke", root=root,
                                    documents=library.list(), jobs=[], read_job=lambda *_: None, attention=[]))
    assert build()["v2-suppliers-slice4"]["state"] == "in progress"
    parses.clear()
    build()
    assert parses == []                                         # nothing changed, nothing parsed again

    prd = root / "v2-suppliers-slice4" / "prd.json"
    data = json.loads(prd.read_text())
    for story in data["userStories"]:
        story["passes"] = True
    prd.write_text(json.dumps(data))
    os.utime(prd, ns=(prd.stat().st_mtime_ns + 10**9,) * 2)
    parses.clear()
    assert build()["v2-suppliers-slice4"]["state"] == "done"
    assert parses == ["prd.json"]


def test_prd_json_reads_as_a_page() -> None:
    document = ProjectLibrary({"restoke": str(RALPH)}).read("restoke", "v2-suppliers-slice4/prd.json")
    assert document["kind"] == "prd"
    html = document["html"]
    assert "userStories" not in html and "acceptanceCriteria" not in html
    assert "Stories · 14 of 15 passing" in html
    assert html.count('checked="checked"') == 14 and html.count('type="checkbox"') == 15
    assert [entry["text"] for entry in document["toc"]] == ["v2-suppliers-slice4/prd.json", "Objectives",
                                                            "Stories · 14 of 15 passing", "Open questions", "Decisions",
                                                            "Out of scope"]
    assert "ralph/v2-suppliers-slice4" in html                   # where it runs: branch, base and worktree


def test_library_shapes_and_paths(tmp_path: Path) -> None:
    ralph = {path.relative_to(RALPH).as_posix() for path in library_paths(RALPH)}
    assert {"v2-suppliers/logs/REPORT-run2.md", "v2-suppliers-slice6/notes/format.md", "v2-suppliers/prd.json",
            "legacy-faults-copied-to-v2.md"} <= ralph
    repo = repository(tmp_path / "repo")
    (repo / "src").mkdir()
    (repo / "src" / "NOTES.md").write_text("# not library")
    assert sorted(path.relative_to(repo).as_posix() for path in library_paths(repo)) == [
        "README.md", "docs/adr/0001-sqlite-store.md", "docs/adr/0005-storehouse.md", "docs/design/workspace-prd.md"]

    library = ProjectLibrary({"restoke": str(RALPH), "repo": str(repo)})
    secret = tmp_path / "secret.md"
    secret.write_text("private")
    (repo / "docs" / "leak.md").symlink_to(secret)
    for project, refused in (("restoke", "../overview_fixture.py"), ("restoke", "v2-suppliers/../../restoke.json"),
                             ("repo", "src/NOTES.md"), ("repo", "docs/leak.md"), ("repo", "notes.txt"),
                             ("restoke", "/etc/passwd.md")):
        assert library.read(project, refused) is None, refused
