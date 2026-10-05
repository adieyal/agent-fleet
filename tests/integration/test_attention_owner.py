import json

import pytest


from fleet.container import configured_container
from fleet.modules.attention import ItemResolved, StreamContext


@pytest.fixture
def attention(tmp_path):
    attention = configured_container(configured_container(path=tmp_path / 'store.db').store()).initialized_attention(workspace_path=tmp_path / 'missing.json')
    attention.mandate = lambda project: object()  # Confirmed availability; these tests cover lifecycle only.
    return attention


def raise_item(attention, reference="r1", **fields):
    return attention.raise_item(**{**dict(project="p1", kind="blocker", owner="user", source="test",
                                          source_reference=reference, headline="Step 1 failed",
                                          context_reference="job:carbon:ab12", actor="host-stream",
                                          subject="job:carbon:ab12"), **fields})


def owner_rows(attention, item):
    return [(row["actor"], json.loads(row["from"]), json.loads(row["to"]))
            for row in attention.repository.store.history_after(0) if row["subject"] == f"attention:{item.id}:owner"]


def test_delegating_hands_the_item_to_the_agent_and_keeps_it_open(attention):
    item = raise_item(attention)
    delegated = attention.delegate(item.id, actor="user", note="retry once")
    assert (delegated.owner, delegated.owner_reason, delegated.owner_actor, delegated.state) == (
        "agent", "retry once", "user", "open")
    assert delegated.owner_at is not None
    assert [item.id for item in attention.list(owner="agent")] == [item.id]
    assert attention.list(owner="user") == []
    assert owner_rows(attention, item) == [("user", {"owner": "user"}, {"owner": "agent", "reason": "retry once"})]


def test_an_agent_escalates_with_a_reason_and_the_user_can_take_an_item_back(attention):
    first, second = raise_item(attention, "r1"), raise_item(attention, "r2")
    for item in (first, second):
        attention.delegate(item.id, actor="user")
    escalated = attention.escalate(first.id, actor="triage", reason="the fix needs a push to GitHub")
    taken = attention.take(second.id, actor="user")
    assert (escalated.owner, escalated.owner_reason, escalated.owner_actor) == (
        "user", "the fix needs a push to GitHub", "triage")
    assert (taken.owner, taken.owner_reason, taken.owner_actor) == ("user", None, "user")
    assert owner_rows(attention, first)[-1] == (
        "triage", {"owner": "agent"}, {"owner": "user", "reason": "the fix needs a push to GitHub"})


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_an_escalation_without_a_reason_is_refused(attention, reason):
    item = raise_item(attention)
    attention.delegate(item.id, actor="user")
    with pytest.raises(ValueError, match="escalation needs a reason"):
        attention.escalate(item.id, actor="triage", reason=reason)
    assert attention.get(item.id).owner == "agent"


def test_hand_overs_need_the_owner_they_hand_from(attention):
    item = raise_item(attention)
    with pytest.raises(ValueError, match="is yours, not the agent"):
        attention.take(item.id, actor="user")
    with pytest.raises(ValueError, match="is yours, not the agent"):
        attention.escalate(item.id, actor="triage", reason="why")
    attention.delegate(item.id, actor="user")
    with pytest.raises(ValueError, match="is with the agent, not yours"):
        attention.delegate(item.id, actor="user")


def test_a_resolved_item_cannot_be_handed_over(attention):
    item = raise_item(attention)
    attention.resolve(item.id, details="retried by hand", actor="user")
    with pytest.raises(ItemResolved):
        attention.delegate(item.id, actor="user")


def test_a_sessions_question_cannot_go_to_an_agent(attention):
    item = raise_item(attention, kind="decision", subject="session:home:s1", stream_context=StreamContext(
        "home", "session", "s1", "p1", "p1", "session tool AskUserQuestion", "claude is asking", 1.0))
    with pytest.raises(ValueError, match="answered only at its terminal"):
        attention.delegate(item.id, actor="user")
    assert attention.get(item.id).owner == "user"


def test_owner_must_be_agent_or_user(attention):
    with pytest.raises(ValueError, match="owner must be agent or user"):
        raise_item(attention, owner="job:carbon:ab12")
    with pytest.raises(ValueError, match="owner must be agent or user"):
        attention.list(owner="orchestrator")
