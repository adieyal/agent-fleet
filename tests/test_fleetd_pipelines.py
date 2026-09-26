"""fleetd's pipeline tracker: incremental aggregation of run event files and what it streams."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fleet.remote import fleetd

NODES = [["invoices"], ["decided", "tied"], ["confident", "review"]]


def line(**record) -> str:
    return json.dumps(record) + "\n"


def run_line(run_id: str, label: str = "orient v4") -> str:
    return line(type="run", run_id=run_id, pipeline="invoice-training", label=label, started_at=100.0,
                nodes=NODES, total=4)


def flow(item: str, source: str, target: str, ts: float = 100.0, **attrs) -> str:
    return line(type="flow", item=item, **{"from": source, "to": target}, ts=ts, **({"attrs": attrs} if attrs else {}))


@pytest.fixture
def pipelines(tmp_path: Path) -> Path:
    (tmp_path / "invoice-training").mkdir()
    return tmp_path


def write(path: Path, text: str, mtime: float | None = None) -> None:
    with open(path, "a") as handle:
        handle.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def edges(summary: dict) -> dict:
    return {(source, target): count for source, target, count in summary["edges"]}


def test_a_run_is_aggregated_and_read_incrementally(pipelines: Path) -> None:
    path = pipelines / "invoice-training" / "20260926T100000-aaaaaa.jsonl"
    write(path, run_line("20260926T100000-aaaaaa") + flow("1", "invoices", "decided") + flow("2", "invoices", "tied"))
    tracker = fleetd.PipelineTracker(pipelines)
    [message] = tracker.scan(clock=105)
    assert message["type"] == "pipeline" and message["pipeline"] == "invoice-training"
    run = message["run"]
    assert (run["label"], run["status"], run["total"], run["flows"]) == ("orient v4", "running", 4, 2)
    assert edges(run) == {("invoices", "decided"): 1, ("invoices", "tied"): 1}
    assert run["counts"] == {"invoices": 2, "decided": 1, "tied": 1}
    assert message["baseline"] is None

    reader = tracker.runs[path]
    offset = reader.offset
    write(path, flow("1", "decided", "confident") + '{"type": "flow", "item": "3", "fr')   # a line mid-write
    [message] = tracker.scan(clock=110)
    assert reader.offset > offset and reader.offset < path.stat().st_size
    assert edges(message["run"])[("decided", "confident")] == 1
    assert message["run"]["flows"] == 3

    write(path, 'om": "invoices", "to": "decided", "ts": 100}\n' + line(type="end", run_id="x", status="done", ts=111))
    [message] = tracker.scan(clock=112)
    assert edges(message["run"])[("invoices", "decided")] == 2
    assert message["run"]["counts"]["invoices"] == 3
    assert message["run"]["status"] == "done"


def test_unlisted_nodes_go_in_the_column_after_their_source(pipelines: Path) -> None:
    path = pipelines / "invoice-training" / "r1.jsonl"
    write(path, run_line("r1") + flow("1", "invoices", "decided") + flow("1", "decided", "review")
          + flow("1", "review", "null cell", reasons=["null cell", "rows do not sum"]))
    [message] = fleetd.PipelineTracker(pipelines).scan(clock=100)
    run = message["run"]
    assert run["nodes"] == NODES + [["null cell"]]
    # only terminal nodes carry their recent items, with attrs
    assert set(run["recent"]) == {"null cell"}
    assert run["recent"]["null cell"] == [{"item": "1", "ts": 100.0, "attrs": {"reasons": ["null cell", "rows do not sum"]}}]


def test_the_previous_finished_run_is_the_baseline_and_older_runs_are_ignored(pipelines: Path) -> None:
    folder = pipelines / "invoice-training"
    write(folder / "r1.jsonl", run_line("r1", "oldest") + flow("1", "invoices", "tied") + line(type="end", status="done"))
    write(folder / "r2.jsonl", run_line("r2", "v3") + flow("1", "invoices", "decided") + flow("2", "invoices", "decided")
          + line(type="end", status="done"))
    write(folder / "r3.jsonl", run_line("r3", "v4") + flow("1", "invoices", "decided"))
    tracker = fleetd.PipelineTracker(pipelines)
    [message] = tracker.scan(clock=100)
    assert message["run"]["run_id"] == "r3"
    assert message["baseline"]["label"] == "v3"
    assert edges(message["baseline"]) == {("invoices", "decided"): 2}
    assert folder / "r1.jsonl" not in tracker.runs

    write(folder / "r4.jsonl", run_line("r4", "v5"))   # r3 never ended, so it is no baseline
    [message] = tracker.scan(clock=102)
    assert message["run"]["run_id"] == "r4" and message["baseline"] is None


def test_a_failed_previous_run_is_no_baseline(pipelines: Path) -> None:
    folder = pipelines / "invoice-training"
    write(folder / "r1.jsonl", run_line("r1") + flow("1", "invoices", "tied") + line(type="end", status="failed"))
    write(folder / "r2.jsonl", run_line("r2"))
    [message] = fleetd.PipelineTracker(pipelines).scan(clock=100)
    assert message["baseline"] is None


def test_updates_are_sent_at_most_once_a_second_and_only_when_changed(pipelines: Path) -> None:
    path = pipelines / "invoice-training" / "r1.jsonl"
    write(path, run_line("r1") + flow("1", "invoices", "decided", ts=50), mtime=60)
    tracker = fleetd.PipelineTracker(pipelines)
    assert len(tracker.scan(clock=100)) == 1
    assert tracker.scan(clock=100.4) == []               # nothing changed
    write(path, flow("2", "invoices", "decided", ts=100.2), mtime=100.3)
    assert tracker.scan(clock=100.8) == []               # changed, but within a second of the last
    [message] = tracker.scan(clock=101.2)                # held back, then sent
    assert message["run"]["flows"] == 2
    assert message["run"]["updated_at"] == 100.3


def test_flows_per_second_cover_the_last_ten_seconds(pipelines: Path) -> None:
    path = pipelines / "invoice-training" / "r1.jsonl"
    write(path, run_line("r1") + "".join(flow(str(k), "invoices", "decided", ts=100 + k * 0.5) for k in range(20))
          + flow("old", "invoices", "tied", ts=10))
    tracker = fleetd.PipelineTracker(pipelines)
    [message] = tracker.scan(clock=109.9)
    assert message["run"]["rate"] == 2.0
    [message] = tracker.scan(clock=200)                  # the file stopped growing: the rate falls to nothing
    assert message["run"]["rate"] == 0.0 and message["run"]["status"] == "running"


def test_a_long_run_is_announced_once_read_to_the_end(pipelines: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleetd, "PIPELINE_READ_BYTES", 400)
    path = pipelines / "invoice-training" / "r1.jsonl"
    write(path, run_line("r1") + "".join(flow(str(k), "invoices", "decided") for k in range(40)))
    tracker = fleetd.PipelineTracker(pipelines)
    scans = [tracker.scan(clock=100 + k) for k in range(30)]
    sent = [message for messages in scans for message in messages]
    assert scans[0] == [] and len(sent) == 1
    assert sent[0]["run"]["flows"] == 40


def test_a_rewritten_file_is_read_again(pipelines: Path) -> None:
    path = pipelines / "invoice-training" / "r1.jsonl"
    write(path, run_line("r1") + flow("1", "invoices", "decided") + flow("2", "invoices", "decided"))
    tracker = fleetd.PipelineTracker(pipelines)
    tracker.scan(clock=100)
    path.write_text(run_line("r1") + flow("1", "invoices", "tied"))
    [message] = tracker.scan(clock=102)
    assert edges(message["run"]) == {("invoices", "tied"): 1}


def test_no_pipelines_directory_means_no_messages(tmp_path: Path) -> None:
    assert fleetd.PipelineTracker(tmp_path / "missing").scan() == []


def test_the_stream_sends_the_latest_run_on_connect(tmp_path: Path) -> None:
    folder = tmp_path / "fleet" / "pipelines" / "invoice-training"
    folder.mkdir(parents=True)
    write(folder / "r1.jsonl", run_line("r1") + flow("1", "invoices", "decided"))
    env = {**os.environ, "FLEET_HOME": str(tmp_path / "fleet"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
           "CODEX_HOME": str(tmp_path / "codex")}
    process = subprocess.Popen([sys.executable, fleetd.__file__, "stream", "--interval", "0.05"], env=env,
                               stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None
        kinds = []
        while "pipeline" not in kinds and len(kinds) < 20:
            message = json.loads(process.stdout.readline())
            kinds.append(message["type"])
        assert kinds[0] == "hello" and "pipeline" in kinds
        assert message["pipeline"] == "invoice-training" and message["run"]["counts"]["decided"] == 1
    finally:
        process.kill()
        process.wait(timeout=5)
