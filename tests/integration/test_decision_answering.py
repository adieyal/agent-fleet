import pytest
import sqlite3
from contextlib import closing

from fleet.composition import open_attention, open_decisions, open_store, open_work
from fleet.infrastructure.sqlite.attention import AttentionRepository
from fleet.infrastructure.sqlite.decisions import DecisionRepository
from fleet.infrastructure.sqlite.store import UnitOfWork, connect
from fleet.infrastructure.sqlite.work import WorkRepository


def setup_question():
    store = open_store()
    work, attention = open_work(store), open_attention(store)
    item = work.add(project="p", title="Deliver", goal="Ship", actor="author")
    work.set(item.id, condition="blocked", actor="author")
    blocker, = attention.list()
    question = attention.raise_item(project="p", work_item=item.id, kind="decision", owner="user",
        source="manual", source_reference="q", headline="Which route?", context_reference="doc:route",
        actor="author", options=("Direct", "Scenic"))
    return store, work, attention, item, question, blocker


def test_decision_repository_requires_records():
    with pytest.raises(TypeError, match='records'):
        DecisionRepository(open_store(), None, None, None)


def test_answer_resolves_exactly_selected_item_and_records_actor_and_unblocks():
    store, work, attention, item, question, blocker = setup_question()
    other_question = attention.raise_item(project="p", work_item=item.id, kind="decision", owner="user",
        source="manual", source_reference="q2", headline="When to leave?", context_reference="doc:time",
        actor="author")
    sequence = store.latest_sequence()
    decision = open_decisions(store).answer(question.id, "2", actor="adi", next_step="Take route")
    assert decision.question == "Which route?"
    assert decision.answer == "Scenic"
    assert decision.actor == "adi"
    assert decision.context == "doc:route"
    assert decision.affected_work_items == (item.id,)
    assert attention.get(question.id).state == "resolved"
    assert attention.get(blocker.id).state == "resolved"
    assert attention.get(other_question.id).state == "open"
    assert work.get(item.id).condition == "none"
    assert work.get(item.id).next_step == "Take route"
    history = store.history_after(sequence)
    assert len(history) == 4
    assert {row["actor"] for row in history} == {"adi"}
    assert open_decisions(open_store()).get(decision.id) == decision
    with pytest.raises(ValueError, match="resolved"):
        open_decisions(store).answer(question.id, "Direct", actor="adi")
    assert store.history_after(sequence) == history


@pytest.mark.parametrize("adapter,method", [(DecisionRepository, "insert"),
    (AttentionRepository, "save"), (WorkRepository, "save"), (UnitOfWork, "record_change")])
def test_failure_anywhere_rolls_back_every_record_and_history(monkeypatch, adapter, method):
    store, work, attention, item, question, blocker = setup_question()
    before = (work.get(item.id), attention.list(), store.latest_sequence())
    original = getattr(adapter, method)

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(adapter, method, fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        open_decisions(store).answer(question.id, "Direct", actor="adi", next_step="Go")
    assert (work.get(item.id), attention.list(), store.latest_sequence()) == before
    assert open_decisions(store).list() == []


def test_invalid_option_does_not_write():
    store, work, attention, item, question, blocker = setup_question()
    sequence = store.latest_sequence()
    with pytest.raises(ValueError, match="option"):
        open_decisions(store).answer(question.id, "3", actor="adi")
    assert store.latest_sequence() == sequence


def test_persisted_decision_refuses_edits_and_deletion():
    store, work, attention, item, question, blocker = setup_question()
    decisions = open_decisions(store)
    decision = decisions.answer(question.id, "Use another route", actor="adi")
    sequence = store.latest_sequence()
    with closing(connect(store.path)) as connection:
        for statement in ("UPDATE decisions_decision SET record = '{}' WHERE id = ?",
                          "DELETE FROM decisions_decision WHERE id = ?"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement, (decision.id,))
    assert decisions.get(decision.id) == decision
    assert store.latest_sequence() == sequence


def test_answer_work_blocker_itself_preserves_next_step():
    store, work, attention, item, question, blocker = setup_question()
    work.set(item.id, next_step="Existing plan", actor="author")
    sequence = store.latest_sequence()
    open_decisions(store).answer(blocker.id, "Proceed", actor="adi")
    assert work.get(item.id).next_step == "Existing plan"
    assert work.get(item.id).condition == "none"
    assert attention.get(question.id).state == "open"
    assert attention.get(blocker.id).state == "resolved"
    history = store.history_after(sequence)
    assert len(history) == 4
    assert {row["actor"] for row in history} == {"adi"}
