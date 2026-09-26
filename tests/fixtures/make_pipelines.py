"""Writes tests/fixtures/pipelines.json: a small recorded fleet with synthetic pipeline runs.

The runs are made up (seeded random items), written as event files and summarised by
fleetd's own PipelineTracker, so the reports have exactly the shape fleetd streams.
Run with: uv run python tests/fixtures/make_pipelines.py
"""
import json
import random
import tempfile
from pathlib import Path

from fleet.remote.fleetd import PipelineTracker

TIME = 1790400000
NODES = [["items"], ["decided", "tied", "unlearnable"], ["alone", "agree", "disagree", "profile", "unsettled"],
         ["confident", "review"], ["null cell", "sum mismatch", "profile disagree", "low support"]]
REASONS = ["null cell", "sum mismatch", "profile disagree", "low support"]


def choose(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), list(weights.values()))[0]


def run_events(run_id: str, label: str, started: float, until: float, items: int, total: int, skew: float, seed: int,
               end: str | None) -> str:
    """Items enter evenly from `started` to `until`; each reaches the gates a few minutes after it entered, so an
    unfinished run has gated only its earlier items. Lines are written in ts order, as a pipeline would."""
    rng = random.Random(seed)
    meta = {"type": "run", "run_id": run_id, "pipeline": "sample-training", "label": label, "started_at": started,
            "nodes": NODES, "total": total}
    lines = []
    for index in range(items):
        item, ts = f"S-{seed}{index:05d}", started + (until - started) * index / items
        first = choose(rng, {"decided": 0.7 + skew, "tied": 0.2, "unlearnable": 0.1 - skew})
        lines.append({"type": "flow", "run_id": run_id, "item": item, "from": "items", "to": first, "ts": ts})
        if first == "unlearnable":
            lines[-1]["attrs"] = {"why": rng.choice(["no printed total", "unreadable scan"])}
            continue
        ts += rng.uniform(1, 20)
        second = choose(rng, {"alone": 0.3, "agree": 0.4, "disagree": 0.15, "profile": 0.15} if first == "decided"
                        else {"agree": 0.3, "unsettled": 0.7})
        lines.append({"type": "flow", "run_id": run_id, "item": item, "from": first, "to": second, "ts": ts})
        ts += rng.uniform(60, 240)
        if not end and ts > until:
            continue   # not at the gates yet
        confident = rng.random() < (0.85 if second in ("alone", "agree", "profile") else 0.25)
        third = "confident" if confident else "review"
        lines.append({"type": "flow", "run_id": run_id, "item": item, "from": second, "to": third, "ts": ts})
        if third == "review":
            reasons = rng.sample(REASONS, rng.randint(1, 3))
            lines.append({"type": "flow", "run_id": run_id, "item": item, "from": "review", "to": reasons[0],
                          "ts": ts + rng.uniform(0.2, 3), "attrs": {"reasons": reasons}})
    lines.sort(key=lambda line: line["ts"])
    if end:
        lines.append({"type": "end", "run_id": run_id, "status": end, "ts": lines[-1]["ts"] + 1})
    return "".join(json.dumps(line) + "\n" for line in [meta, *lines])


def reports() -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory) / "sample-training"
        folder.mkdir()
        (folder / "20260926T100000-b1.jsonl").write_text(
            run_events("20260926T100000-b1", "synthetic baseline", TIME - 9000, TIME - 6000, 2600, 2600, -0.04, 1, "done"))
        (folder / "20260926T120000-r2.jsonl").write_text(
            run_events("20260926T120000-r2", "synthetic run", TIME - 1200, TIME - 4, 2400, 3000, 0.04, 2, None))
        messages = PipelineTracker(Path(directory)).scan(clock=TIME)
    for message in messages:
        message["run"]["updated_at"] = TIME - 4   # as though the file was last written just before the recording
    return [{"host": "home", "pipeline": message["pipeline"], "run": message["run"], "baseline": message["baseline"]}
            for message in messages]


def fixture() -> dict:
    return {
        "time": TIME,
        "project_labels": {},
        "projects": {},
        "hosts": [
            {"name": "home", "ok": True, "error": None, "sessions": [], "jobs": [
                {"id": "a1b2c3", "host": "home", "project": "restoke", "description": "Tidy the supplier import",
                 "agent": "claude", "model": "claude-opus-5-5", "status": "running", "cwd": "~/src/restoke",
                 "created_at": TIME - 900, "updated_at": TIME - 20, "session_id": None, "tmux": None,
                 "steps": [{"index": 0, "title": "Tidy the supplier import", "status": "running", "result": None,
                            "started_at": TIME - 900, "finished_at": None}],
                 "todos": [], "events": [], "activity": None, "documents": []}]},
            {"name": "worker", "ok": False, "error": "worker: ssh: connect to host worker port 22: Connection refused",
             "jobs": [], "sessions": []},
        ],
        "pipelines": {"sample-training": {"host": "home", "project": "sample-training"},
                      "embeddings": {"host": "home", "project": "restoke"},
                      "nightly-eval": {"host": "worker", "project": "nightly-eval"}},
        "pipeline_reports": reports(),
    }


def main() -> None:
    path = Path(__file__).parent / "pipelines.json"
    path.write_text(json.dumps(fixture(), indent=1) + "\n")
    print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
