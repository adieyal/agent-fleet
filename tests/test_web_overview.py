"""A project's overview, derived from its library folders and its stored jobs."""

import json
import os
import shutil
from pathlib import Path


from fleet.container import configured_container
from fleet_web.fixture import FixtureLibrary
from fleet.services.fixtures import FixtureState
from fleet_web.library import ProjectLibrary
from fleet.infrastructure.documents.library import library_paths
from overview_fixture import AGENT_FLEET, RALPH, overview_fixture, repository


NOW = 1790400000.0   # the recorded fleet's time
DAY = 86400


def aged(root: Path, seconds: float) -> Path:
    """Every file under `root` last changed `seconds` before NOW."""
    for path in [root, *root.rglob("*")]:
        os.utime(path, (NOW - seconds, NOW - seconds))
    return root


def ralph_copy(tmp_path: Path, seconds: float = 2 * DAY) -> Path:
    return aged(shutil.copytree(RALPH, tmp_path / "ralph"), seconds)


def restoke(root: Path, jobs: list[dict] | None = None) -> dict:
    library = ProjectLibrary({'restoke': {'path': str(root), 'recursive': True}}, container=configured_container())   # as Restoke is configured
    return configured_container().overview().build(name='Restoke', project_id=None, library='restoke', root=library.root('restoke'), documents=library.list(), jobs=jobs or [], read_job=lambda *_: None, attention=[], now=NOW)


def job(job_id: str, status: str, cwd: str, description: str) -> dict:
    return {"key": f"home-{job_id}", "host": "home", "id": job_id, "status": status, "cwd": cwd,
            "description": description, "created_at": NOW - 3600, "updated_at": NOW - 600, "documents": [], "steps": []}


def by_id(overview: dict) -> dict:
    return {stream["id"]: stream for stream in overview["workstreams"]}


def test_workstreams_come_from_ralph_folders(tmp_path: Path) -> None:
    overview = restoke(ralph_copy(tmp_path))
    streams = by_id(overview)
    # nothing has changed in two days, so the part-done slice 4 is paused, not in progress
    assert [(stream["id"], stream["state"]) for stream in overview["workstreams"]] == [
        ("v2-review", "paused"), ("v2-suppliers-slice4", "paused"), ("v2-suppliers-slice6", "blocked"),
        ("v2-suppliers", "done"), ("v2-suppliers-slice5", "done")]
    assert all(stream["last_activity"] == NOW - 2 * DAY for stream in overview["workstreams"])

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

    assert overview["summary"] == ("5 workstreams: 2 paused, 1 blocked, 2 done. Nothing is active right now. "
                                   "71 open questions wait for you.")
    # a folder of Markdown alone is a workstream, named by its newest file; loose files are listed as they are
    assert streams["v2-review"]["title"] == "V2 architecture review — adi/v2-suppliers @ 39afc88cc"
    assert [(group["folder"], [doc["trace"]["id"] for doc in group["documents"]]) for group in overview["other_documents"]] == [
        (".", ["legacy-faults-copied-to-v2.md"])]


def test_recent_changes_or_a_waiting_job_keep_work_in_progress(tmp_path: Path) -> None:
    root = ralph_copy(tmp_path)
    progress = root / "v2-suppliers-slice4" / "progress.txt"      # not a library document, still a sign of work
    progress.write_text("US-071 started")
    os.utime(progress, (NOW - 3600, NOW - 3600))
    slice4 = by_id(restoke(root))["v2-suppliers-slice4"]
    assert (slice4["state"], slice4["last_activity"]) == ("in progress", NOW - 3600)

    os.utime(progress, (NOW - DAY - 1, NOW - DAY - 1))            # a day and a second ago is too long
    assert by_id(restoke(root))["v2-suppliers-slice4"]["state"] == "paused"

    queued = job("5e11ce", "queued", "/home/adi/Development/restoke/webapp/worktrees/v2-suppliers-slice4", "Finish slice 4")
    slice4 = by_id(restoke(root, [queued]))["v2-suppliers-slice4"]
    assert (slice4["state"], slice4["last_activity"]) == ("in progress", NOW - 600)


def test_a_spike_folder_and_its_running_job_are_active_and_no_running_job_is_other_work(tmp_path: Path) -> None:
    root = tmp_path / "ralph"
    (root / "spike-one-shell").mkdir(parents=True)
    (root / "spike-one-shell" / "SPIKE.md").write_text("# Spike: one shell for every V2 page\n\nA spike.\n")
    (root / "notes-only").mkdir()
    (root / "notes-only" / "older.md").write_text("# Older notes\n")
    (root / "notes-only" / "newer.md").write_text("# Newer notes\n")
    aged(root, 3 * DAY)
    os.utime(root / "notes-only" / "newer.md", (NOW - 2 * DAY, NOW - 2 * DAY))
    jobs = [job("aa0001", "running", "/home/adi/Development/restoke/webapp/worktrees/spike-one-shell/", "Try one shell"),
            job("aa0002", "running", "/home/adi/Development/restoke", "Upgrade Django"),
            job("aa0003", "queued", "/home/adi/Development/restoke", "Tune tolerance"),
            job("aa0004", "done", "/home/adi/Development/restoke", "Write an article")]
    overview = restoke(root, jobs)
    streams = by_id(overview)
    spike = streams["spike-one-shell"]
    assert (spike["title"], spike["state"]) == ("Spike: one shell for every V2 page", "in progress")
    assert [linked["id"] for linked in spike["jobs"]] == ["aa0001"]    # linked by its worktree's name
    assert [trace["label"] for trace in spike["traces"]] == ["SPIKE.md"]
    assert (streams["notes-only"]["title"], streams["notes-only"]["state"]) == ("Newer notes", "paused")
    assert streams["notes-only"]["last_activity"] == NOW - 2 * DAY
    # running and waiting jobs linked to nothing are cards of their own under Active; finished ones are other work
    assert [(stream["id"], stream["title"], stream["state"]) for stream in overview["workstreams"]] == [
        ("spike-one-shell", "Spike: one shell for every V2 page", "in progress"),
        ("job:home-aa0002", "Upgrade Django", "in progress"), ("job:home-aa0003", "Tune tolerance", "in progress"),
        ("notes-only", "Newer notes", "paused")]
    assert [[entry["id"] for entry in week["jobs"]] for week in overview["other_work"]] == [["aa0004"]]


def test_jobs_link_by_worktree_or_brief_and_the_rest_are_other_work(tmp_path: Path) -> None:
    state = FixtureState(overview_fixture(repository(tmp_path / 'agent-fleet')), container=configured_container())
    projects = {project["name"]: project for project in state.library_overview(FixtureLibrary(state.fixture, container=configured_container()))}

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

    # the running jobs linked to no folder are cards of their own; only the failed one is other work
    assert [stream["title"] for stream in projects["Restoke"]["workstreams"] if stream["id"].startswith("job:")] == [
        "Shadow-parse 60 invoices and diff against Textract", "Migrate the suppliers list to a V2 React route"]
    assert [(week["week"], [job["id"] for job in week["jobs"]]) for week in projects["Restoke"]["other_work"]] == [
        ("Week of 21 Sep 2026", ["e1b5c8"])]

    # agent-fleet has no Ralph folders: its job is a workstream by description, its docs are listed by folder
    fleet = projects["agent-fleet"]
    assert fleet["project_id"] == AGENT_FLEET
    [stream] = fleet["workstreams"]
    assert (stream["title"], stream["state"]) == ("Split the deck into ES modules", "in progress")
    assert fleet["other_work"] == []
    # a failed job is blocked and says why; a queued one is active
    invoices = {stream["title"]: stream for stream in projects["Invoice analysis"]["workstreams"]}
    assert invoices["Collect credit-note samples"]["state"] == "blocked"
    assert invoices["Tune LUC tolerance for credit notes"]["state"] == "in progress"
    assert [(group["folder"], len(group["documents"])) for group in fleet["other_documents"]] == [
        (".", 1), ("docs/adr", 2), ("docs/design", 1)]


def test_the_overview_follows_document_changes_and_reuses_unchanged_parses(tmp_path: Path, monkeypatch) -> None:
    root = ralph_copy(tmp_path)
    library = ProjectLibrary({'restoke': str(root)}, container=configured_container())
    overview = configured_container().overview()
    parses = []
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: parses.append(self.name) or real_read(self, *a, **k))

    def build():
        return by_id(overview.build(name="Restoke", project_id=None, library="restoke", root=root,
                                    documents=library.list(), jobs=[], read_job=lambda *_: None, attention=[], now=NOW))
    assert build()["v2-suppliers-slice4"]["state"] == "paused"
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
    document = ProjectLibrary({'restoke': str(RALPH)}, container=configured_container()).read('restoke', 'v2-suppliers-slice4/prd.json')
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

    library = ProjectLibrary({'restoke': str(RALPH), 'repo': str(repo)}, container=configured_container())
    secret = tmp_path / "secret.md"
    secret.write_text("private")
    (repo / "docs" / "leak.md").symlink_to(secret)
    for project, refused in (("restoke", "../overview_fixture.py"), ("restoke", "v2-suppliers/../../restoke.json"),
                             ("repo", "src/NOTES.md"), ("repo", "docs/leak.md"), ("repo", "notes.txt"),
                             ("restoke", "/etc/passwd.md")):
        assert library.read(project, refused) is None, refused


def test_a_recursive_library_feeds_the_overview_and_never_shows_local_notes(tmp_path: Path) -> None:
    """Restoke V2 is configured {"path": ".../webapp/ralph", "recursive": true}."""
    root = aged(shutil.copytree(RALPH, tmp_path / "webapp" / "ralph"), 2 * DAY)
    (root / "CLAUDE.local.md").write_text("# private notes")
    (root / "v2-suppliers-slice6" / "notes" / "mine.local.md").write_text("# private notes")
    (root / "worktrees" / "v2-slice6").mkdir(parents=True)
    (root / "worktrees" / "v2-slice6" / "README.md").write_text("# a checkout, not a document")
    library = ProjectLibrary({'restoke': {'path': str(root), 'recursive': True}}, container=configured_container())
    documents = library.list()
    ids = {document["id"] for document in documents}
    assert {"v2-suppliers-slice6/notes/format.md", "v2-suppliers/prd.json", "v2-suppliers/logs/REPORT-run2.md"} <= ids
    assert not [name for name in ids if name.endswith(".local.md") or name.startswith("worktrees/")]
    for private in ("CLAUDE.local.md", "v2-suppliers-slice6/notes/mine.local.md"):
        assert library.read("restoke", private) is None

    overview = configured_container().overview().build(name='Restoke', project_id=None, library='restoke', root=library.root('restoke'), documents=documents, jobs=[], read_job=lambda *_: None, attention=[], now=NOW)
    assert {key: stream["state"] for key, stream in by_id(overview).items()} == {
        "v2-review": "paused", "v2-suppliers": "done", "v2-suppliers-slice4": "paused", "v2-suppliers-slice5": "done",
        "v2-suppliers-slice6": "blocked"}
    traces = [trace["trace"]["id"] for trace in by_id(overview)["v2-suppliers-slice6"]["traces"]]
    assert "v2-suppliers-slice6/notes/mine.local.md" not in traces
    listed = [doc["trace"]["id"] for group in overview["other_documents"] for doc in group["documents"]]
    assert "CLAUDE.local.md" not in listed


def test_a_recursive_repository_lists_nested_folders_but_no_local_notes(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    (repo / "src" / "deep").mkdir(parents=True)
    (repo / "src" / "deep" / "NOTES.md").write_text("# Deep notes")
    (repo / "CLAUDE.local.md").write_text("# private")
    (repo / "docs" / "adr" / "draft.local.md").write_text("# private")
    flat = {doc["id"] for doc in ProjectLibrary({'repo': str(repo)}, container=configured_container()).list()}
    deep = {doc["id"] for doc in ProjectLibrary({'repo': {'path': str(repo), 'recursive': True}}, container=configured_container()).list()}
    assert "src/deep/NOTES.md" not in flat and "src/deep/NOTES.md" in deep
    assert not [name for name in flat | deep if name.endswith(".local.md")]
    library = ProjectLibrary({'repo': {'path': str(repo), 'recursive': True}}, container=configured_container())
    assert library.read("repo", "src/deep/NOTES.md")["markdown"] == "# Deep notes"
    assert library.read("repo", "CLAUDE.local.md") is None
    overview = configured_container().overview().build(name='repo', project_id=None, library='repo', root=library.root('repo'), documents=library.list(), jobs=[], read_job=lambda *_: None, attention=[])
    assert overview["workstreams"] == []
    assert [group["folder"] for group in overview["other_documents"]] == [".", "docs/adr", "docs/design", "src/deep"]
