"""The canvas kernel against a real controller store: every gesture is one operation the kernel accepts or
refuses, refusals name their source line, and runs move through the scheduler."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from fleet.api import FleetError
from fleet.container import configured_container


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def space(clock, monkeypatch):
    monkeypatch.delenv("FLEET_JOB_ID", raising=False)
    container = configured_container(clock=clock)
    project = container.initialized_workspace().edit_registry(lambda registry: registry.create("canvas-test")).id
    canvas = container.canvas()
    assert canvas.init(project, actor="user")["result"] == {"created": True}
    return SimpleNamespace(container=container, canvas=canvas, project=project, clock=clock)


def op(space, operation, actor="user", op_id=None, **args):
    return space.canvas.operation(space.project, operation, args, actor=actor, op_id=op_id)


def ok(space, operation, actor="user", **args):
    result = op(space, operation, actor=actor, **args)
    assert result.get("ok"), result
    return result["result"]


def state(space):
    return space.canvas.state(space.project, person="user")


def item(space, identity):
    return next(entry for entry in state(space)["items"] if entry["id"] == identity)


def task(space, title="Task", **args):
    return ok(space, "item.create", title=title, **args)["item"]


def simulate(space, seconds=10):
    for agent in ("claude", "codex"):
        ok(space, "agent.configure", agent=agent, mode="simulate", seconds=seconds)


def test_init_is_idempotent_and_starts_the_default_workflow(space):
    again = space.canvas.init(space.project, actor="user")
    assert again["result"] == {"created": False}
    model = state(space)
    assert [stage["id"] for stage in model["workflow"]["stages"]] == ["plan", "implement", "approve"]
    assert model["workflow"]["flow"] == "Plan → Implement → Approve → Done"
    assert [region["name"] for region in model["regions"]] == ["Inbox"]
    assert model["log"][0]["text"].startswith("adopted by you")


def test_a_project_without_a_canvas_is_refused(space):
    other = space.container.initialized_workspace().edit_registry(lambda registry: registry.create("plain")).id
    result = space.canvas.operation(other, "tick", {}, actor="user")
    assert result["refused"] and result["code"] == "not_found"


def test_new_items_land_in_the_placement_region_and_notify(space):
    identity = task(space, "Write the client")
    card = item(space, identity)
    assert card["region"] == "inbox" and card["stage"] is None and card["status"] == "idle"
    model = state(space)
    assert model["notes"][0]["text"] == "New in Inbox: Write the client"
    assert model["notes"][0]["why"] == "zone Inbox v1 · line 5"


def test_work_items_made_outside_the_canvas_are_adopted_on_the_next_tick(space):
    created = space.container.work().add(project=space.project, title="From the CLI", goal="Arrives", actor="user")
    ok(space, "tick")
    assert item(space, created.id)["region"] == "inbox"


def test_moves_are_refused_with_the_code_that_refused_them(space):
    identity = task(space)
    skipped = op(space, "item.move", item=identity, stage="implement")
    assert skipped["refused"] and skipped["code"] == "stage_skipped"
    assert skipped["message"] == "New items enter at Plan."
    ok(space, "item.move", item=identity, stage="plan")
    unmet = op(space, "item.move", item=identity, stage="implement", op_id="c-7f3a")
    assert unmet == {"refused": True, "code": "exit_condition_unmet",
                     "message": "Task cannot leave Plan: “spec has acceptance criteria” is not true yet.",
                     "source": {"object": "stage plan", "version": 1, "line": 5},
                     "condition": "spec has acceptance criteria", "op_id": "c-7f3a"}
    log = state(space)["log"]
    assert log[-1]["tone"] == "refuse" and log[-1]["source"] == "stage plan v1" and log[-1]["line"] == 5
    assert op(space, "item.move", item=identity, stage="implement", op_id="c-7f3a")["replayed"]


def test_criteria_let_an_item_leave_plan_and_dispatch_a_builder(space):
    identity = task(space, criteria=["Tests pass"], stage="first")
    card = item(space, identity)
    assert card["stage"] == "implement" and card["owner"] == "planner"
    assert card["status"] == "queued"
    assert "no host is set up for claude or codex" in card["next"]


def test_idempotent_writes_apply_once(space):
    first = op(space, "item.create", op_id="same", title="Once")
    second = op(space, "item.create", op_id="same", title="Once")
    assert second["replayed"] and second["result"] == first["result"]
    assert [entry["title"] for entry in state(space)["items"]] == ["Once"]


def test_every_write_names_an_actor(space):
    result = op(space, "tick", actor="")
    assert result["refused"] and result["code"] == "invalid"


def test_scheduler_runs_builders_to_approval_and_you_approve_to_done(space):
    simulate(space, seconds=5)
    identity = task(space, criteria=["Tests pass"], stage="first")
    card = item(space, identity)
    assert card["status"] == "working" and card["run"]["agent"] == "claude" and card["run"]["simulated"]
    space.clock.advance(6)
    ok(space, "tick")
    card = item(space, identity)
    assert card["stage"] == "approve" and card["status"] == "waiting-you" and card["facts"]["submitted"]
    approval = next(entry for entry in state(space)["attention"] if entry["item"] == identity)
    fleet_item = space.container.attention().get(approval["fleet"])
    assert fleet_item.state == "open" and fleet_item.source == "canvas" and fleet_item.options == ("Approve", "Send back")
    ok(space, "attention.resolve", id=approval["id"], choice="approve")
    card = item(space, identity)
    assert card["stage"] == "done" and card["status"] == "done"
    assert space.container.work().get(identity).condition == "complete"
    decision = next(entry for entry in space.container.decisions().list() if entry.attention_item == approval["fleet"])
    assert decision.actor == "user" and decision.answer == "Approve"


def test_an_approval_answered_outside_the_canvas_is_applied(space):
    simulate(space, seconds=1)
    identity = task(space, criteria=["Tests pass"], stage="first")
    space.clock.advance(2)
    ok(space, "tick")
    approval = next(entry for entry in state(space)["attention"] if entry["item"] == identity)
    space.container.decisions().answer(approval["fleet"], "2", actor="user")  # option 2 is Send back
    ok(space, "tick")
    assert item(space, identity)["stage"] == "implement"


def test_testers_go_to_an_agent_other_than_the_builder(space):
    simulate(space, seconds=1)
    ok(space, "stage.draft")
    ok(space, "workflow.insert", block="test", index=2)
    identity = task(space, criteria=["Tests pass"], stage="first")
    assert item(space, identity)["run"]["agent"] == "claude"
    space.clock.advance(2)
    ok(space, "tick")
    card = item(space, identity)
    assert card["stage"] == "test" and card["run"]["role"] == "tester" and card["run"]["agent"] == "codex"
    space.clock.advance(2)
    ok(space, "tick")
    card = item(space, identity)
    assert card["facts"]["evidence"] == {"tests pass": True} and card["stage"] == "approve"


def test_capacity_and_dependencies_hold_runs_in_the_queue(space):
    ok(space, "agent.configure", agent="claude", mode="simulate", seconds=30)
    first = task(space, "First", criteria=["a"], stage="first")
    second = task(space, "Second", criteria=["b"], stage="first")
    third = task(space, "Third", criteria=["c"], stage="first")
    assert item(space, third)["next"] == "Queued: claude 2/2 busy"
    ok(space, "dep.add", **{"from": first, "to": third})
    assert item(space, third)["next"] == "Waiting on First"
    cycle = op(space, "dep.add", **{"from": third, "to": first})
    assert cycle["refused"] and cycle["code"] == "cycle"
    relations = space.container.work().relations(third)
    assert [(relation.from_item, relation.to_item) for relation in relations] == [(third, first)]
    ok(space, "dep.remove", **{"from": first, "to": third})
    assert space.container.work().relations(third) == []
    assert item(space, second)["status"] == "working"


def test_band_limits_refuse_a_full_band(space):
    items = [task(space, f"Task {index}") for index in range(3)]
    ok(space, "item.set_band", item=items[0], band="now")
    ok(space, "item.set_band", item=items[1], band="now")
    full = op(space, "item.set_band", item=items[2], band="now")
    assert full["code"] == "capacity_full" and full["source"] == {"object": "schedule", "version": 1, "line": 6}
    assert full["message"] == "Now already holds 2 unfinished items."


def test_regions_run_their_code_and_guard_their_capacity(space):
    proposal = ok(space, "region.propose", name="Parked", rect={"x": 0, "y": 0, "w": 400, "h": 300})
    assert proposal["compiled"]["object"] == "zone"
    created = ok(space, "proposal.resolve", id=proposal["proposal"], adopt=True, level="enforced")
    parked = created["results"][0]["region"]
    identity = task(space, "Park me")
    ok(space, "region.enter", region=parked, item=identity)
    card = item(space, identity)
    assert card["status"] == "paused" and card["budget"] == 0 and card["next"] == "Paused by Parked"
    assert op(space, "run.resume", item=identity)["code"] == "not_permitted"
    agent = op(space, "region.exit", actor="job:abc", region=parked, item=identity)
    assert agent["code"] == "not_permitted" and agent["source"]["line"] == 11
    ok(space, "region.exit", region=parked, item=identity)
    assert item(space, identity)["status"] == "idle"
    context = ok(space, "region.create", name="Context for agents", rect={"x": 600, "y": 0, "w": 400, "h": 300})
    wrong = op(space, "region.enter", region=context["region"], item=identity)
    assert wrong["code"] == "wrong_kind"
    ok(space, "context.add", doc={"kind": "doc", "id": "spec", "title": "Spec"}, region=context["region"])
    assert [entry["title"] for entry in state(space)["context"]] == ["Spec"]


def test_agents_may_only_propose_entry_where_the_region_says_so(space):
    created = ok(space, "region.create", name="Next", rect={"x": 0, "y": 0, "w": 400, "h": 300})
    identity = task(space)
    proposed = ok(space, "region.enter", actor="job:abc", region=created["region"], item=identity)
    assert "proposal" in proposed and item(space, identity)["region"] == "inbox"
    ok(space, "proposal.resolve", id=proposed["proposal"], adopt=True)
    assert item(space, identity)["region"] == created["region"]


def test_compiling_versions_code_and_refuses_a_stale_base(space):
    result = ok(space, "code.compile", object={"kind": "stage", "id": "implement"}, base=1,
                text="stage implement\n  on enter:\n    dispatch builder\n    use the warm image\n"
                     "  exit when:\n    revision submitted")
    assert result["version"] == 2 and result["compiled"]["guided"] == 1
    stale = op(space, "code.compile", object={"kind": "stage", "id": "implement"}, base=1, text="stage implement")
    assert stale["code"] == "version_conflict" and stale["current"]["version"] == 2
    renamed = op(space, "code.compile", object={"kind": "stage", "id": "implement"}, text="stage build")
    assert renamed["code"] == "invalid" and "must stay: stage implement" in renamed["message"]
    versions = space.canvas.versions(space.project, "stage", "implement")
    assert [entry["version"] for entry in versions] == [2, 1]
    schedule = ok(space, "code.compile", object={"kind": "schedule"}, text="schedule v1\n  capacity:\n    claude: 3 runs")
    assert schedule["version"] == 2
    assert state(space)["schedule"]["code"].startswith("schedule v2")


def test_inserting_a_stage_migrates_or_pins_work_in_flight(space):
    moved = task(space, "Moved", criteria=["a"], stage="first")
    pinned = task(space, "Pinned", criteria=["b"], stage="first")
    ok(space, "stage.draft")
    unchosen = op(space, "workflow.insert", block="test", index=1)
    assert unchosen["code"] == "invalid" and "migration" in unchosen["message"]
    ok(space, "workflow.insert", block="test", index=1, migration="pin")
    model = state(space)
    assert model["workflow"]["version"] == 2 and model["workflow"]["flow"] == "Plan → Test → Implement → Approve → Done"
    assert item(space, moved)["pin"] == 1 and item(space, pinned)["pin"] == 1
    refused = op(space, "item.move", item=moved, stage="test")
    assert refused["code"] == "stage_skipped" and "finishes under workflow v1" in refused["message"]


def test_epics_shape_until_every_criterion_is_covered(space):
    epic = ok(space, "epic.create", title="Phase 1", criteria=["Fast", "Small"])["epic"]
    model = state(space)
    fast, small = [criterion["id"] for criterion in model["epics"][0]["criteria"]]
    child = task(space, "Speed", epic=epic, criteria=["p95 under 20ms"], covers=[fast], stage="first")
    assert "still in Shape" in item(space, child)["next"]
    decomposition = ok(space, "epic.decompose", epic=epic)
    ok(space, "proposal.resolve", id=decomposition["proposal"], adopt=True)
    model = state(space)
    assert model["epics"][0]["stage"] == "deliver" and model["epics"][0]["gaps"] == 0
    titles = {entry["title"] for entry in model["items"]}
    assert "Small" in titles


def test_moving_a_card_to_another_epic_drops_its_coverage(space):
    first = ok(space, "epic.create", title="One", criteria=["A"])["epic"]
    second = ok(space, "epic.create", title="Two", criteria=["B"])["epic"]
    criterion = state(space)["epics"][0]["criteria"][0]["id"]
    child = task(space, "Child", epic=first, criteria=["x"], covers=[criterion])
    result = ok(space, "item.reparent", item=child, epic=second)
    assert "covers none of its criteria" in result["warning"]
    assert item(space, child)["covers"] == [] and item(space, child)["epic"] == second


def test_criteria_changes_lose_evidence_and_bump_the_spec(space):
    identity = task(space, criteria=["One"])
    before = state(space)["spec"]["version"]
    ok(space, "criteria.set", item=identity, list=["One", "Two"])
    ok(space, "criteria.set", item=identity, list=["Two"])
    assert [criterion.text for criterion in space.container.work().criteria(identity)] == ["Two"]
    assert state(space)["spec"]["version"] == before + 2


def test_charter_rules_outrank_the_decision_scope(space):
    ok(space, "charter.update", patch={"scope": {"destructive": "decide"}}, base=1)
    assert ok(space, "decision.check", kind="destructive")["level"] == "decide"
    ok(space, "charter.update", patch={"add_clause": {"text": "Deleting images needs you", "kind": "enforced",
                                                      "rule": "Deleting images needs you"}})
    check = ok(space, "decision.check", kind="destructive")
    assert check == {"level": "ask", "scope": "decide", "outranked_by": "Deleting images needs you",
                     "code": "rule_outranks_scope", "charter_version": 3}
    stale = op(space, "charter.update", patch={"north_star": "Ship"}, base=1)
    assert stale["code"] == "version_conflict"


def test_messages_become_guidance_and_proposals(space):
    identity = task(space)
    ok(space, "message.send", text="Use the warm image; don't rebuild cold", target={"kind": "task", "id": identity})
    card = item(space, identity)
    assert card["guidance"][0]["text"] == "Use the warm image; don't rebuild cold"
    reply = ok(space, "message.send", text="what's costing the most?")
    model = state(space)
    assert [message["who"] for message in model["messages"]["orch"]] == ["user", "orchestrator"]
    ok(space, "proposal.resolve", id=reply["proposals"][0], adopt=True)
    view = state(space)["views"][0]
    assert view["type"] == "table" and view["options"] == {"columns": "cost", "sort": "cost"}


def test_the_brief_cites_guidance_with_its_source(space):
    ok(space, "code.compile", object={"kind": "stage", "id": "implement"},
       text="stage implement\n  on enter:\n    dispatch builder\n    use the warm image\n  exit when:\n    revision submitted")
    identity = task(space, "Build it", criteria=["Tests pass"], stage="first")
    ok(space, "message.send", text="Prefer small commits", target={"kind": "task", "id": identity})
    run = item(space, identity)["run"]["id"]
    with space.container.canvas().scope() as (facades, canvas):
        brief = canvas.engine(space.project, space.canvas.ports(facades, space.project), actor="test").brief(run)
    assert "You are the builder for the work item “Build it”" in brief
    assert "- Tests pass" in brief
    assert "- [stage implement v2, line 4] use the warm image" in brief
    assert "- [guidance from user on the task] Prefer small commits" in brief


def test_dispatch_starts_real_runs_and_retries_a_failure(space, monkeypatch):
    sent = []

    class Dispatch:
        def send(self, request, steps):
            sent.append((request, steps))
            if len(sent) == 1:
                raise FleetError("host worker has no label for this project")
            return {"intent": SimpleNamespace(run=SimpleNamespace(id="fleet-run-1", host="worker", remote_job_id=request.id))}

    space.canvas.dispatch = lambda: Dispatch()
    ok(space, "agent.configure", agent="claude", mode="dispatch", host="worker", cwd="/src/app", permission="acceptEdits")
    identity = task(space, criteria=["Tests pass"], stage="first")
    assert item(space, identity)["run"]["state"] == "starting"
    space.canvas.tick(space.project)
    card = item(space, identity)
    assert card["run"]["state"] == "queued" and "host worker has no label" in card["run"]["queue_reason"]
    space.clock.advance(61)
    space.canvas.tick(space.project)
    card = item(space, identity)
    assert card["run"]["fleet_run"] == "fleet-run-1" and card["run"]["host"] == "worker"
    request, steps = sent[-1]
    assert (request.host, request.agent, request.cwd, request.work_item) == ("worker", "claude", "/src/app", identity)
    assert request.id == card["run"]["id"] + "-1" and request.permission == "acceptEdits"
    assert steps[0]["prompt"].startswith("You are the builder")


def test_layout_is_personal_and_unlogged(space):
    before = len(state(space)["log"])
    space.canvas.layout(space.project, "user", "epic:abc", {"x": 10, "y": 20})
    space.canvas.layout(space.project, "someone", "epic:abc", {"x": 99, "y": 99})
    assert state(space)["layout"] == {"epic:abc": {"x": 10, "y": 20}}
    assert len(state(space)["log"]) == before


def test_real_runs_follow_their_fleet_run(space, monkeypatch):
    class Dispatch:
        def send(self, request, steps):
            return {"intent": SimpleNamespace(run=SimpleNamespace(id="fleet-run", host="worker", remote_job_id=request.id))}

    space.canvas.dispatch = lambda: Dispatch()
    ok(space, "agent.configure", agent="claude", mode="dispatch", host="worker", cwd="/src/app")
    ok(space, "agent.configure", agent="codex", mode="dispatch", host="worker", cwd="/src/app")
    identity = task(space, criteria=["Tests pass"], stage="first")
    space.canvas.tick(space.project)
    fleet = SimpleNamespace(status="running", reason=None, usage={"cost_usd": 0.42}, start=None, end=None,
                            runtime="claude", current_action="Editing facade.py")
    open_items = []
    real_ports = space.canvas.ports

    def ports(facades, project):
        bound = real_ports(facades, project)
        bound.run = lambda run_id: fleet if run_id == "fleet-run" else None
        bound.run_attention = lambda run_id: open_items
        return bound

    monkeypatch.setattr(space.canvas, "ports", ports)
    space.canvas.tick(space.project)
    card = item(space, identity)
    assert card["status"] == "working" and card["spent"] == 0.42
    assert card["run"]["fleet"]["current_action"] == "Editing facade.py"
    open_items.append(SimpleNamespace(id="a1", state="open", refusals=("pytest",), headline="pytest refused"))
    space.canvas.tick(space.project)
    card = item(space, identity)
    assert card["status"] == "struggling" and card["run"]["excerpt"] == "pytest refused"
    unblock = next(entry for entry in state(space)["attention"] if entry["kind"] == "Unblock")
    assert unblock["item"] == identity
    open_items.clear()
    fleet.status, fleet.reason = "failed", "blocked"
    space.canvas.tick(space.project)
    assert item(space, identity)["status"] == "blocked"
    fleet.status, fleet.reason = "succeeded", None
    space.canvas.tick(space.project)
    card = item(space, identity)
    assert card["facts"]["submitted"] and card["stage"] == "approve"


def test_layout_set_is_an_operation_too(space):
    result = op(space, "layout.set", object="epic:e1", props={"x": 5, "y": 6})
    assert result["ok"]
    assert state(space)["layout"] == {"epic:e1": {"x": 5, "y": 6}}


def test_host_answers_name_an_attention_item_of_this_space(space):
    result = op(space, "attention.answer", id="missing", answer="Use the warm image")
    assert result["refused"] and result["code"] == "invalid"


def dispatching(space):
    started, cancelled = [], []

    class Dispatch:
        def send(self, request, steps):
            started.append(request.id)
            return {"intent": SimpleNamespace(run=SimpleNamespace(id=f"fleet-{len(started)}", host="worker",
                                                                  remote_job_id=request.id))}

    class Jobs:
        def cancel(self, host, job, *, all_steps):
            cancelled.append(job)

    space.canvas.dispatch = lambda: Dispatch()
    space.canvas.jobs = lambda: Jobs()
    space.canvas.transport = SimpleNamespace(host_by_name=lambda name: name)
    ok(space, "agent.configure", agent="claude", mode="dispatch", host="worker", cwd="/src")
    return started, cancelled


def test_a_run_can_be_paused_again_after_it_resumes(space, monkeypatch):
    started, cancelled = dispatching(space)
    real_ports = space.canvas.ports

    def ports(facades, project):
        bound = real_ports(facades, project)
        jobs = {f"fleet-{index + 1}": job for index, job in enumerate(started)}
        bound.run = lambda run_id: SimpleNamespace(status="stopped" if jobs.get(run_id) in cancelled else "running",
                                                   reason=None, usage=None) if run_id in jobs else None
        bound.run_attention = lambda run_id: []
        return bound

    monkeypatch.setattr(space.canvas, "ports", ports)
    identity = task(space, criteria=["a"], stage="first")
    space.canvas.tick(space.project)
    ok(space, "run.pause", item=identity)
    space.canvas.tick(space.project)
    space.canvas.tick(space.project)
    ok(space, "run.resume", item=identity)
    space.canvas.tick(space.project)
    assert item(space, identity)["run"]["state"] == "starting"
    space.canvas.tick(space.project)
    assert item(space, identity)["status"] == "working"
    ok(space, "run.pause", item=identity)
    space.canvas.tick(space.project)
    assert len(set(started)) == 2 and started[1].endswith("-2") and len(cancelled) == 2


def test_pausing_before_the_run_reaches_its_host_dispatches_nothing(space):
    started, cancelled = dispatching(space)
    identity = task(space, criteria=["a"], stage="first")
    assert item(space, identity)["run"]["state"] == "starting"
    ok(space, "run.pause", item=identity)
    space.canvas.tick(space.project)
    assert started == [] and cancelled == [] and item(space, identity)["status"] == "paused"


def test_the_tick_operation_ticks_once(space):
    now = ok(space, "region.create", name="Now", rect={"x": 0, "y": 0, "w": 600, "h": 300})["region"]
    nxt = ok(space, "region.create", name="Next", rect={"x": 700, "y": 0, "w": 300, "h": 500})["region"]
    for index in range(3):
        identity = task(space, f"Queued {index}")
        ok(space, "region.enter", region=nxt, item=identity)
    ok(space, "region.configure", region=now, level="label")
    ok(space, "region.configure", region=now, level="enforced")
    pulled = [entry for entry in state(space)["items"] if entry["region"] == now]
    before = len(pulled)
    ok(space, "tick")
    assert len([entry for entry in state(space)["items"] if entry["region"] == now]) <= before + 1


def test_resuming_a_spent_budget_grants_a_fresh_allowance(space):
    simulate(space, seconds=100)
    identity = task(space, criteria=["a"], stage="first")
    with space.canvas.scope() as (facades, canvas):
        engine = canvas.engine(space.project, space.canvas.ports(facades, space.project), actor="user")
        engine.state(identity)["budget"] = 0.5
        canvas.commit(space.project, engine)
    space.clock.advance(60)
    ok(space, "tick")
    card = item(space, identity)
    assert card["status"] == "paused" and card["paused_by"] == "budget"
    ok(space, "run.resume", item=identity)
    ok(space, "tick")
    card = item(space, identity)
    assert card["status"] == "working" and card["budget"] == pytest.approx(1.1)


def test_an_approval_closed_without_an_answer_is_reopened(space):
    simulate(space, seconds=1)
    identity = task(space, criteria=["a"], stage="first")
    space.clock.advance(2)
    ok(space, "tick")
    approval = next(entry for entry in state(space)["attention"] if entry["item"] == identity)
    space.container.attention().resolve(approval["fleet"], details="tidied away", actor="user")
    ok(space, "tick")
    assert item(space, identity)["stage"] == "approve"
    again = next(entry for entry in state(space)["attention"] if entry["item"] == identity)
    assert again["fleet"] != approval["fleet"] and space.container.attention().get(again["fleet"]).state == "open"
    assert "asked again" in state(space)["log"][-1]["text"]


def test_sending_an_epic_back_follows_its_own_workflow(space):
    ok(space, "code.compile", object={"kind": "epicflow"}, text='epic workflow v1\nstage shape\n  exit when:\n'
       '    every criterion covered by a child\nstage build\n  exit when:\n    all children in done\nstage accept\n'
       '  on enter:\n    ask you "Accept {item}?"\n  exit when:\n    you approve')
    epic = ok(space, "epic.create", title="E", criteria=["c"])["epic"]
    criterion = state(space)["epics"][0]["criteria"][0]["id"]
    simulate(space, seconds=1)
    child = task(space, "Child", epic=epic, criteria=["x"], covers=[criterion], stage="first")
    space.clock.advance(2)
    ok(space, "tick")
    ok(space, "attention.resolve", id=next(entry["id"] for entry in state(space)["attention"] if entry["item"] == child),
       choice="approve")
    accept = next(entry for entry in state(space)["attention"] if entry["epic"] == epic and entry["kind"] == "Accept")
    ok(space, "attention.resolve", id=accept["id"], choice="back")
    assert state(space)["epics"][0]["stage"] == "build"


def test_removing_a_view_takes_it_out_of_context(space):
    context = ok(space, "region.create", name="Context for agents", rect={"x": 0, "y": 0, "w": 400, "h": 300})["region"]
    view = ok(space, "view.place", type="note", x=10, y=10)["view"]
    ok(space, "context.add", doc={"kind": "view", "id": view, "title": "Note"}, region=context)
    ok(space, "view.remove", view=view)
    assert state(space)["context"] == []
