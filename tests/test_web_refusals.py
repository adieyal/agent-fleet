"""A job's refused permission requests: one item per step, answered by changing the job's permissions."""
import argparse
import importlib.util
import io
import json
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fleet.modules.attention import AttentionItem, StreamContext
from fleet_worker import fleetd
from fleet.services.live import apply_message
from test_web_attention import HOSTS, Deck, job

REQUESTS = [("Bash", "git status --short", "Check worktree state", ["Bash(git status:*)"]),
            ("Bash", "git diff", "", ["Bash(git diff:*)"]),
            ("Bash", "git status", "", ["Bash(git status:*)"]),
            ("Bash", "pytest -q", "Run tests", ["Bash(pytest:*)"]),
            ("Bash", "ls docs", "", ["Bash(ls:*)"]),
            ("Bash", "cd /srv && make", "", ["Bash(cd:*)", "Bash(make:*)"]),
            ("Read", "/etc/restoke.conf", "", ["Read(//etc/restoke.conf)"])]


def refusal(occurrence, tool="Bash", detail="git status", description="", rules=("Bash(git status:*)",),
            step=1, job_id="j1", owner_type="job", at=200.0, kind="input_requested"):
    request = {"tool": tool, "description": description, "detail": detail}
    if rules is not None:
        request["rules"] = list(rules)
    return {"type": "input_observation", "schema_version": 1, "runtime": "claude", "owner_type": owner_type,
            "job_id": job_id if owner_type == "job" else None, "session_id": "s1",
            "step_index": step if owner_type == "job" else None, "project": "restoke", "kind": kind,
            "reason": "permission", "source_event": "PermissionRequest" if kind == "input_requested" else "PostToolUse",
            "source_event_id": occurrence, "observed_at": at, "context_reference": "/retained/hook.json",
            "request": request}


def post(deck, path, body):
    request = Request(deck.url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def decision(deck, item_id):
    with urlopen(deck.url + "/api/decision?id=" + item_id, timeout=5) as response:
        return json.load(response)


@pytest.fixture
def deck(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {"python": sys.executable}, "gpu": {"ssh": "gpu.example"}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    deck = Deck()
    apply_message(deck.state, HOSTS[0], {"type": "hello"})
    yield deck
    deck.close()


def test_a_jobs_refusals_gather_into_one_item_per_step(deck):
    for index, (tool, detail, description, rules) in enumerate(REQUESTS):
        for _ in range(2):   # fleetd replays every observation on reconnect
            apply_message(deck.state, HOSTS[0], refusal(f"r{index}", tool, detail, description, rules, at=200 + index))
    apply_message(deck.state, HOSTS[0], refusal("other", step=2))
    apply_message(deck.state, HOSTS[0], refusal("asked", owner_type="session"))
    apply_message(deck.state, HOSTS[0], refusal("asked again", owner_type="session", detail="git diff"))
    items = deck.state.attention.list()
    batch = next(item for item in items if item.source_reference == "job:home:j1:step:1")
    assert batch.headline == "restoke step 2: 7 commands refused (Bash ×6, Read ×1)"
    assert len(batch.headline.split()) <= 12
    assert (batch.kind, batch.state) == ("decision", "open")
    assert batch.last_seen.timestamp() == 206
    assert [item.source_reference for item in items if item.stream_context.owner_type == "job"].count(
        "job:home:j1:step:2") == 1
    assert len([item for item in items if item.stream_context.owner_type == "session"]) == 2  # a person is there
    listed = {item["id"]: item for item in json.loads(json.dumps(deck.state.document()["attention"]))}
    assert listed[batch.id]["summary"] == batch.headline and listed[batch.id]["refusals"] == 7
    detail = decision(deck, batch.id)["refusals"]
    assert (detail["job"], detail["step"], detail["host"]) == ("j1", 1, "home")
    assert [(request["tool"], request["detail"], request["description"]) for request in detail["requests"]] == [
        (tool, text, description) for tool, text, description, _ in REQUESTS]
    assert detail["rules"] == ["Bash(git status:*)", "Bash(git diff:*)", "Bash(pytest:*)", "Bash(ls:*)",
                               "Bash(cd:*)", "Bash(make:*)", "Read(//etc/restoke.conf)"]
    # A request that ran after all (PostToolUse) leaves the batch.
    apply_message(deck.state, HOSTS[0], refusal("r6", "Read", kind="input_cleared", at=300))
    assert deck.state.attention.get(batch.id).headline == "restoke step 2: 6 commands refused (Bash ×6)"


def test_a_headline_with_many_tools_stays_within_twelve_words(deck):
    for index, tool in enumerate(["Bash", "Read", "Write", "WebFetch", "Grep"]):
        apply_message(deck.state, HOSTS[0], refusal(f"r{index}", tool, rules=(tool,)))
    [batch] = deck.state.attention.list()
    assert batch.headline == "restoke step 2: 5 commands refused (Bash ×1, Read ×1, +3 more)"


def test_a_batch_stays_answerable_after_its_step_until_the_job_moves_on(deck):
    apply_message(deck.state, HOSTS[0], refusal("r1"))
    apply_message(deck.state, HOSTS[0], refusal("r2", step=0, job_id="gone"))
    [batch] = [item for item in deck.state.attention.list() if item.stream_context.owner_id == "j1"]
    for steps in ([("done", 100), ("running", 150)],                  # its step still runs
                  [("done", 100), ("failed", 150)],                   # its step ended: now is when it is noticed
                  [("done", 100), ("failed", 150), ("pending", None)]):  # a queued step has not started
        apply_message(deck.state, HOSTS[0], {"type": "job", "job": job("j1", "failed", steps)})
        apply_message(deck.state, HOSTS[0], {"type": "heartbeat"})
        assert deck.state.attention.get(batch.id).state == "open"
    moved_on = job("j1", "running", [("done", 100), ("failed", 150), ("running", 300)])
    apply_message(deck.state, HOSTS[0], {"type": "job", "job": moved_on})
    closed = deck.state.attention.get(batch.id)
    assert (closed.state, closed.resolution_details) == ("resolved", "refused; the job went on to step 3")
    # A job the host no longer reports (finished long ago, or removed) closes at the first full report.
    [gone] = [item for item in deck.state.attention.list() if item.stream_context.owner_id == "gone"]
    assert deck.state.attention.get(gone.id).resolution_details == "refused; job finished or removed"
    apply_message(deck.state, HOSTS[0], refusal("r3"))   # a late replay does not reopen it
    assert deck.state.attention.get(batch.id).state == "resolved"


def test_per_request_job_items_from_before_are_folded_or_superseded(deck):
    def legacy(occurrence):
        owner = "job:home:j1"
        item = AttentionItem(id=occurrence, project="restoke", work_item=None, run=None, kind="decision",
                             owner="user", subject=owner, source="runtime-input:home",
                             source_reference=f"{owner}:{occurrence}",
                             headline="Claude asks to use Bash", context_reference="git status", state="open",
                             snooze_until=None, resolution_details=None, last_seen=deck.state.attention.clock(),
                             stream_context=StreamContext("home", "job", "j1", "restoke", None,
                                                          "Claude permission request", "Claude asks to use Bash", 1))
        with deck.state.attention.repository.transaction() as transaction:
            transaction.save(item, None, "test")
    legacy("old1")
    legacy("old2")
    apply_message(deck.state, HOSTS[0], refusal("old1"))   # replayed: its step is known now
    assert deck.state.attention.get("old1").resolution_details == "folded into step 2's refusals"
    assert deck.state.attention.get("old2").state == "open"
    apply_message(deck.state, HOSTS[0], {"type": "job", "job": job("j1", "running", [("done", 1), ("running", 2)])})
    apply_message(deck.state, HOSTS[0], {"type": "heartbeat"})
    assert deck.state.attention.get("old2").resolution_details == "superseded: job refusals are gathered per step"
    [batch] = [item for item in deck.state.attention.list() if item.state == "open"]
    assert [refusal.occurrence for refusal in batch.refusals] == ["old1"]


def test_dismiss_resolves_and_free_text_is_refused(deck):
    apply_message(deck.state, HOSTS[0], refusal("r1"))
    [batch] = deck.state.attention.list()
    assert post(deck, "/api/decision/answer", {"id": batch.id, "answer": "yes"})[0] == 400
    assert post(deck, "/api/attention/dismiss", {"id": batch.id}) == (
        200, {"id": batch.id, "resolution": "dismissed; the job's permissions are unchanged"})
    assert deck.state.attention.get(batch.id).state == "resolved"
    assert post(deck, "/api/attention/dismiss", {"id": batch.id})[0] == 409
    assert post(deck, "/api/attention/allow", {"id": batch.id, "scope": "bash"})[0] == 409


def test_an_older_fleetd_without_rules_can_only_allow_all_bash(deck, monkeypatch):
    from fleet import transport
    sent = []

    def call(host, arguments, stdin_text=None):   # the worker, as fleetd answers a grant
        sent.append((host.name, arguments, json.loads(stdin_text)))
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied",
                "added": ["Bash"], "continuation": 3}
    monkeypatch.setattr(transport, "call", call)
    apply_message(deck.state, HOSTS[0], refusal("r1", rules=None))
    [batch] = deck.state.attention.list()
    assert decision(deck, batch.id)["refusals"]["rules"] is None
    status, body = post(deck, "/api/attention/allow", {"id": batch.id, "scope": "refused"})
    assert status == 400 and "names no rules" in body["error"]
    assert deck.state.attention.get(batch.id).state == "open" and not sent
    status, body = post(deck, "/api/attention/allow", {"id": batch.id, "scope": "bash"})
    assert status == 200
    assert body["resolution"] == "allowed for job j1: Bash; step 2 continues as step 4"
    assert sent == [("home", ["grant", "j1", "--schema-version", "1", "--step", "1", "--key", f"{batch.id}:bash"],
                     ["Bash"])]


def isolated_worker(tmp_path, monkeypatch):
    """This machine as a worker: fleetd in its own FLEET_HOME, tmux server and a stand-in for claude."""
    home = tmp_path / "worker"
    monkeypatch.setenv("FLEET_HOME", str(home))
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    monkeypatch.setenv("FLEET_FLEETD_PATH", fleetd.__file__)
    spec = importlib.util.spec_from_file_location("worker_fleetd", fleetd.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    claude = tmp_path / "claude"
    claude.write_text(f"#!{sys.executable}\nimport json, sys\n"
                      f"open({str(home / 'argv.jsonl')!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                      "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's1'}))\n"
                      "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,"
                      " 'result': 'FLEET_STATUS: done', 'session_id': 's1'}))\n")
    claude.chmod(0o755)
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"claude": str(claude)}))
    steps = tmp_path / "steps.json"
    steps.write_text('["Review the repository"]')
    module.command_create(argparse.Namespace(id="j1", run_id=None, fingerprint=None, schema_version=None,
        cwd=str(tmp_path), agent="claude", permission="default", steps_file=str(steps), project="restoke",
        description="Restoke review", model=None, effort=None, bare=False, keep_going=False, allowed_tools='["Bash(ls:*)"]',
        add_dir=[], env=[], hold=True))
    return module


def wait_until_finished(worker):
    deadline = time.monotonic() + 20
    while worker.derive_status(worker.read_job("j1")) not in worker.TERMINAL_STATUSES or worker.runner_alive(
            worker.read_job("j1")):
        assert time.monotonic() < deadline, "runner did not finish"
        time.sleep(0.05)


@pytest.mark.parametrize("scope, rules", [("refused", ["Bash(git status:*)", "Bash(ls:*)", "Read(//etc/restoke.conf)"]),
                                          ("bash", ["Bash"])])
def test_allowing_reaches_the_job_through_fleetd(deck, tmp_path, monkeypatch, capsys, scope, rules):
    worker = isolated_worker(tmp_path, monkeypatch)
    try:
        # Step 1 runs and is refused three things, recorded by the hook as Claude would call it.
        worker.launch_runner("j1")
        wait_until_finished(worker)
        for tool, tool_input in [("Bash", {"command": "git status --short"}), ("Bash", {"command": "ls docs"}),
                                 ("Read", {"file_path": "/etc/restoke.conf"})]:
            worker.record_input_hook({"hook_event_name": "PermissionRequest", "session_id": "s1",
                                      "tool_name": tool, "tool_input": tool_input}, project="restoke",
                                     job_id="j1", step_index=0)
        for observation in worker.input_observations():
            apply_message(deck.state, HOSTS[0], {"type": "input_observation", **observation})
        [batch] = deck.state.attention.list()
        status, body = post(deck, "/api/attention/allow", {"id": batch.id, "scope": scope})
        assert status == 200, body
        assert body["resolution"] == f"allowed for job j1: {', '.join(rules)}; step 1 continues as step 2" + (
            "; still not allowed: /etc/restoke.conf" if scope == "bash" else "")
        assert deck.state.attention.get(batch.id).resolution_details == body["resolution"]
        wait_until_finished(worker)
        job_file = worker.read_job("j1")
        assert job_file["allowed_tools"] == ["Bash(ls:*)"] + [rule for rule in rules if rule != "Bash(ls:*)"]
        assert [step["title"] for step in job_file["steps"]] == ["Review the repository", "Continue step 1"]
        assert job_file["steps"][1]["status"] == "done"
        first, continued = [json.loads(line) for line in (tmp_path / "worker" / "argv.jsonl").read_text().splitlines()]
        allowed = continued[continued.index("--allowedTools") + 1:continued.index("--resume")]
        assert allowed == list(dict.fromkeys([*job_file["allowed_tools"], *worker.FLEET_READ_TOOLS]))
        assert 'Bash(fleet:*)' not in allowed
        assert 'Bash(fleet decision record:*)' not in allowed
        assert continued[continued.index("--resume") + 1] == "s1"
        # The same grant again (a lost reply retried) queues nothing more.
        capsys.readouterr()
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(rules)))
        worker.command_grant(argparse.Namespace(job="j1", step=0, key=f"{batch.id}:{scope}", schema_version=1))
        assert len(worker.read_job("j1")["steps"]) == 2
    finally:
        worker.subprocess.run([*worker.TMUX_COMMAND, "kill-server"], capture_output=True)


def test_a_request_a_deny_rule_refuses_is_not_offered_as_allowable(deck, monkeypatch):
    from fleet import transport
    sent = []

    def call(host, arguments, stdin_text=None):
        sent.append(json.loads(stdin_text))
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied",
                "added": [], "continuation": 2}
    monkeypatch.setattr(transport, "call", call)
    denied = refusal("curl", detail="curl -s https://example.com", rules=("Bash(curl:*)",))
    denied["request"]["denied_by"] = ["Bash(curl:*) in /home/me/.claude/settings.json"]
    apply_message(deck.state, HOSTS[0], denied)
    [batch] = deck.state.attention.list()
    detail = decision(deck, batch.id)["refusals"]
    assert detail["rules"] == [] and detail["requests"][0]["denied_by"] == ["Bash(curl:*) in /home/me/.claude/settings.json"]
    for scope in ("refused", "bash"):
        status, body = post(deck, "/api/attention/allow", {"id": batch.id, "scope": scope})
        assert status == 400 and "no rule allowed for the job can override it" in body["error"], body
    assert not sent and deck.state.attention.get(batch.id).state == "open"
    # Beside an allowable request, the denied one stays out of the rules and is named as still refused.
    apply_message(deck.state, HOSTS[0], refusal("ls", detail="ls docs", rules=("Bash(ls:*)",)))
    status, body = post(deck, "/api/attention/allow", {"id": batch.id, "scope": "refused"})
    assert status == 200 and sent == [["Bash(ls:*)"]]
    assert body["resolution"] == ("allowed for job j1: Bash(ls:*); step 2 continues as step 3; still not allowed: "
                                  "curl -s https://example.com (denied by Bash(curl:*) in /home/me/.claude/settings.json)")
