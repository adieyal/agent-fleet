"""A job step that ends blocked asks its question on the deck, and the deck's answer continues the job."""
import json
import sys

import pytest

from fleet.container import configured_container
from fleet import transport

from fleet.infrastructure.answers import send_answer
from fleet.modules.attention import ItemResolved
from fleet.modules.execution import AnswerRequest
from fleet.modules.attention.application.observations import asking
from fleet.services.live import apply_message
from test_web_attention import HOSTS, Deck, job
from test_web_refusals import decision, isolated_worker, post, wait_until_finished

QUESTION = ("May I exempt the existing `main.containers` import and continue? [SPLIT.md](context/SPLIT.md) says to "
            "stop and report imports from the excluded list.\n\nFLEET_STATUS: blocked — needs your decision")


def blocked(job_id="b1", message=QUESTION, started=110):
    reported = job(job_id, "blocked", [("done", 100), ("blocked", started)])
    reported["steps"][1]["message"] = message
    return reported


@pytest.fixture
def deck(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {"python": sys.executable}, "gpu": {"ssh": "gpu.example"}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    deck = Deck()
    apply_message(deck.state, HOSTS[0], {"type": "hello"})
    yield deck
    deck.close()


def only_item(deck):
    [item] = deck.state.attention.list()
    return item


@pytest.mark.parametrize("message, asked", [
    (QUESTION, "May I exempt the existing `main.containers` import and continue?"),
    ("Checked the tree.\n\n```\nwhy?\n```\nShall I drop the flag? Or keep it?", "Shall I drop the flag?"),
    ("I need the staging token.\nFLEET_STATUS: blocked", "I need the staging token."),
])
def test_what_a_message_asks(message, asked):
    assert asking(message) == asked


def test_a_blocked_step_carries_its_question(deck):
    deck.report("home", jobs=[blocked()])
    item = only_item(deck)
    assert (item.kind, item.headline) == ("blocker", "step 2 asks: May I exempt the existing `main.containers` import and continue?")
    assert item.stream_context.message == QUESTION
    assert item.stream_context.step == 1 and item.stream_context.blocked_step
    assert deck.items()["home:b1"]["blocked"] is True
    assert decision(deck, item.id)["blocked"] == {"host": "home", "job": "b1", "step": 1, "message": QUESTION,
                                                  "state": "open", "resolution": None}


def test_an_older_fleetd_leaves_the_question_unknown(deck):
    deck.report("home", jobs=[blocked(message=None)])
    item = only_item(deck)
    assert item.headline == "step 2 blocked: step 1" and item.stream_context.message is None
    assert decision(deck, item.id)["blocked"]["message"] is None   # still answerable, the deck says it is unknown


def test_failed_and_stalled_jobs_are_not_answerable(deck):
    deck.report("home", jobs=[job("f1", "failed", [("failed", 100)]), job("s1", "stalled", [("running", 120)])])
    assert not any(item.stream_context.blocked_step for item in deck.state.attention.list())
    assert not any(item["blocked"] for item in deck.items().values())


def test_answering_adds_a_step_and_resolves(deck):
    deck.report("home", jobs=[blocked()])
    item, sent = only_item(deck), []
    execution = configured_container(deck.state.store).execution()
    execution.answer = lambda request: sent.append(request) or 2
    details = execution.answer_blocked(item.id, "Yes, exempt it.", actor="user")
    assert details == "answered; step 2 continues as step 3"
    [request] = sent
    assert (request.host, request.job, request.step, request.key, request.reply) == (
        "home", "b1", 1, f"{item.id}:answer", "Yes, exempt it.")
    resolved = deck.state.attention.get(item.id)
    assert (resolved.state, resolved.resolution_details) == ("resolved", details)
    with pytest.raises(ItemResolved):
        execution.answer_blocked(item.id, "Again", actor="user")
    assert len(sent) == 1
    deck.report("home", jobs=[blocked()])   # still reported blocked until the answer runs: stays resolved
    assert deck.state.attention.get(item.id).state == "resolved"


@pytest.mark.parametrize("reply, error", [("   ", "an answer is required"),
                                          ("Go on", "only a blocked job step can be answered here")])
def test_only_a_blocked_step_takes_an_answer(deck, reply, error):
    deck.report("home", jobs=[job("f1", "failed", [("failed", 100)])])
    execution = configured_container(deck.state.store).execution()
    execution.answer = lambda request: pytest.fail("nothing is sent")
    with pytest.raises(ValueError, match=error):
        execution.answer_blocked(only_item(deck).id, reply, actor="user")
    manual = deck.state.attention.raise_item(project="p", kind="decision", owner="user", source="manual",
        source_reference="m1", headline="Pick one", context_reference="notes.md", actor="user")
    with pytest.raises(ValueError, match="only a blocked job step"):
        execution.answer_blocked(manual.id, "Go on", actor="user")


def test_the_endpoint_sends_the_reply_through_fleetd_add(deck, monkeypatch):
    sent = []

    def call(host, arguments, stdin_text=None):   # the worker, as fleetd answers a keyed add
        sent.append((host.name, arguments, json.loads(stdin_text)))
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied",
                "answers": 1, "steps": [2]}
    monkeypatch.setattr(transport, "call", call)
    deck.report("home", jobs=[blocked()])
    item = only_item(deck)
    assert post(deck, "/api/attention/answer", {"id": item.id}) == (
        400, {"error": "the item's id and an answer are required"})
    status, body = post(deck, "/api/attention/answer", {"id": item.id, "answer": "Yes, exempt it."})
    assert (status, body) == (200, {"id": item.id, "resolution": "answered; step 2 continues as step 3"})
    assert sent == [("home", ["add", "b1", "--steps-file", "/dev/stdin", "--schema-version", "1",
                              "--key", f"{item.id}:answer", "--answers", "1"],
                     [{"prompt": "Yes, exempt it.", "title": "Answer to step 2"}])]
    assert post(deck, "/api/attention/answer", {"id": item.id, "answer": "Again"})[0] == 409
    assert post(deck, "/api/attention/answer", {"id": "missing", "answer": "Hi"})[0] == 404


def test_an_answer_may_name_the_work_its_step_serves(deck, monkeypatch):
    sent = []

    def call(host, arguments, stdin_text=None):
        sent.append(json.loads(stdin_text))
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied",
                "answers": 1, "steps": [2]}
    monkeypatch.setattr(transport, "call", call)
    deck.report("home", jobs=[blocked()])
    item = only_item(deck)
    milestone = configured_container(deck.state.store).work().add(project='p', title='M2', goal='Next part', actor='user')
    assert post(deck, "/api/attention/answer", {"id": item.id, "answer": "Go", "work_item": 3})[0] == 400
    assert post(deck, "/api/attention/answer", {"id": item.id, "answer": "Go", "work_item": "w-missing"})[0] == 404
    assert sent == []
    assert post(deck, "/api/attention/answer", {"id": item.id, "answer": "Go", "work_item": milestone.id})[0] == 200
    assert sent == [[{"prompt": "Go", "title": "Answer to step 2", "work_item": milestone.id}]]


def test_an_unconfirmed_answer_leaves_the_item_open(deck, monkeypatch):
    monkeypatch.setattr(transport, "call", lambda host, arguments, stdin_text=None: {"status": "queued"})
    deck.report("home", jobs=[blocked()])
    item = only_item(deck)
    status, body = post(deck, "/api/attention/answer", {"id": item.id, "answer": "Yes"})
    assert (status, body["error"]) == (400, "worker did not confirm the answer")
    assert deck.state.attention.get(item.id).state == "open"


def test_answering_reaches_the_job_through_fleetd(deck, tmp_path, monkeypatch):
    worker = isolated_worker(tmp_path, monkeypatch)
    try:
        # The stand-in claude says done; record the first step as having ended blocked with a question.
        worker.launch_runner("j1")
        wait_until_finished(worker)
        with worker.locked_job("j1") as live:
            live["steps"][0].update(status="blocked", reason="needs your decision")
        (tmp_path / "worker" / "jobs" / "j1" / "result-0.md").write_text(QUESTION)
        reported = worker.job_summary(worker.read_job("j1"), 0)
        deck.report("home", jobs=[reported])
        item = only_item(deck)
        assert item.stream_context.message == QUESTION
        status, body = post(deck, "/api/attention/answer", {"id": item.id, "answer": "Yes, exempt it."})
        assert (status, body["resolution"]) == (200, "answered; step 1 continues as step 2")
        wait_until_finished(worker)
        job_file = worker.read_job("j1")
        assert [(step["title"], step["status"]) for step in job_file["steps"]] == [
            ("Review the repository", "blocked"), ("Answer to step 1", "done")]
        assert job_file["steps"][0]["answered_by"] == 1 and worker.derive_status(job_file) == "done"
        # The same answer again (a lost reply retried) reports the first step and queues nothing more.
        assert send_answer(AnswerRequest("home", "j1", 0, f"{item.id}:answer", "Yes, exempt it.")) == 1
        assert len(worker.read_job("j1")["steps"]) == 2
    finally:
        worker.subprocess.run([*worker.TMUX_COMMAND, "kill-server"], capture_output=True)


def test_batch11_blocked_answer_records_one_decision_after_confirmation(deck):

    deck.report('home', jobs=[blocked()])
    item = only_item(deck)
    execution = configured_container(deck.state.store).execution()
    execution.answer = lambda request: 2
    execution.answer_blocked(item.id, 'Yes, exempt it.', actor='reviewer')
    [record] = configured_container(deck.state.store).decisions().list()
    assert (record.attention_item, record.question, record.answer, record.actor) == (
        item.id, item.headline, 'Yes, exempt it.', 'reviewer')
    assert record.context == item.context_reference and record.principle is None
    from fleet.projections.decisions import decision_log
    decisions = configured_container(deck.state.store).decisions()
    assert [d['id'] for d in decision_log(configured_container(deck.state.store).work(), decisions, project=item.project)] == [record.id]
    assert decision_log(configured_container(deck.state.store).work(), decisions, project='other') == []
    with pytest.raises(ItemResolved):
        execution.answer_blocked(item.id, 'Again', actor='reviewer')
    assert len(configured_container(deck.state.store).decisions().list()) == 1


def test_batch11_blocked_answer_retains_run_work_and_project_for_unlinked_questions(deck):

    from fleet.projections.decisions import decision_log
    deck.report('home', jobs=[blocked()])
    item = only_item(deck)
    work = configured_container(deck.state.store).work()
    task = work.add(project='p', title='Blocked work', goal='Finish', actor='user')
    execution = configured_container(deck.state.store).execution()
    run = execution.link('home', 'b1', task.id, actor='user')
    execution.answer = lambda request: 2
    execution.answer_blocked(item.id, 'Proceed', actor='user')
    decisions = configured_container(deck.state.store).decisions()
    [record] = decisions.list()
    assert record.source_run == run.id and record.affected_work_items == (task.id,)
    assert [d['id'] for d in decision_log(work, decisions, project='p')] == [record.id]


def test_batch11_unconfirmed_answer_records_no_decision(deck):

    deck.report('home', jobs=[blocked()])
    item = only_item(deck)
    execution = configured_container(deck.state.store).execution()
    def refuse(request):
        raise RuntimeError('worker did not confirm')
    execution.answer = refuse
    with pytest.raises(RuntimeError, match='did not confirm'):
        execution.answer_blocked(item.id, 'Proceed', actor='user')
    assert configured_container(deck.state.store).decisions().list() == []
    assert deck.state.attention.get(item.id).state == 'open'
