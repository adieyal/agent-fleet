import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet import cli, composition
from fleet.remote import fleetd
from fleet.web.ingester import observe_runs


@pytest.mark.parametrize("agent,tokens,cost", [
    ("claude", {"input_tokens": 120, "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 50, "output_tokens": 24}, 0.0123),
    ("codex", {"input_tokens": 240, "cached_input_tokens": 80, "output_tokens": 42}, None),
    ("claude", None, None),
    ("codex", None, None),
])
def test_recorded_results_reach_durable_run_without_replay_churn(tmp_path, monkeypatch, capsys, agent, tokens, cost):
    monkeypatch.setattr(fleetd, "JOBS_DIRECTORY", tmp_path)
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(fleetd.signal, "signal", lambda *args: None)
    records = (Path(__file__).parents[1] / "fixtures" / f"runtime_{agent}_result.jsonl").read_text()
    if tokens is None:
        records = '\n'.join(json.dumps({k: v for k, v in json.loads(line).items()
                                       if k not in ("usage", "total_cost_usd")}) for line in records.splitlines())
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(pid=123, stdout=StringIO(records), wait=lambda: 0)

    monkeypatch.setattr(fleetd.subprocess, "Popen", popen)
    job = {"id": "job", "agent": agent, "project": "p", "description": "Usage",
           "cwd": str(tmp_path), "permission": "read-only", "created_at": 1,
           "steps": [fleetd.make_step(0, "Proceed", "First")], "runner_pid": None}
    (tmp_path / "job").mkdir()
    (tmp_path / "job" / "job.json").write_text(json.dumps(job))
    fleetd.run_job("job")
    summary = fleetd.job_summary(fleetd.read_job("job"), 0)
    assert summary["status"] == "done"
    store = composition.open_store()
    work = composition.open_work(store)
    item = work.add(project="p", title="Usage", goal="Capture", actor="user")
    execution = composition.open_execution(store)
    linked = execution.link("host", "job", item.id, actor="user")
    library = composition.open_library(store)
    host = {"ok": True, "name": "host", "jobs": {"job": summary}}
    observe_runs(execution, library, host)
    run = composition.open_execution(composition.open_store()).get_run(linked.id)
    if tokens is None:
        assert run.usage is None
    else:
        assert run.usage.source == {"claude": "claude.result", "codex": "codex.turn.completed"}[agent]
        assert run.usage.reports == [{"tokens": tokens, "cost_usd": cost}]
    sequence = store.latest_sequence()
    observe_runs(execution, library, host)
    assert store.latest_sequence() == sequence
    assert execution.get_run(run.id) == run
    assert len(commands) == 1
    cli.main(["status", "p"])
    output = capsys.readouterr().out
    assert "Usage: unknown" in output if tokens is None else run.usage.source in output
    cli.main(["status", "p", "--json"])
    projected = json.loads(capsys.readouterr().out)["work_items"][0]["runs"][0]
    assert projected["usage"] == summary["usage"]


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_runtime_seam_resumes_answer_in_same_session(tmp_path, monkeypatch, agent):
    monkeypatch.setattr(fleetd, "CONFIG_PATH", tmp_path / "config.json")
    job = {"id": "job", "agent": agent, "permission": "read-only", "cwd": str(tmp_path), "project": "p"}
    command = fleetd._runtime(agent).command(job, {"index": 1, "prompt": "Answer"}, "session")
    assert "session" in command and "Answer" in command
    assert "--resume" in command if agent == "claude" else command[1:3] == ["exec", "resume"]
