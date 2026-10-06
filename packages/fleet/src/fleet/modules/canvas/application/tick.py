"""What the kernel does every tick: adopt new work, follow runs, advance, schedule."""
from __future__ import annotations

from datetime import timedelta

from .kernel import ACTIVE_RUN, BUSY_RUN, Kernel, iso, parse_time

APPROVALS = ("approve", "approved", "accept", "accepted", "yes", "y", "ok", "1")
RANK = {"now": 0, "next": 1, "later": 2}
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
SIMULATED_SECONDS = 20
RETRY_SECONDS = 60


class Ticking(Kernel):
    def tick(self) -> dict:
        self.adopt_new_work()
        self.sync_attention()
        self.sync_runs()
        self.enforce_budgets()
        for _ in range(4):
            moved = False
            for identity in self.card_ids():
                state = self.state(identity)
                if state.get("paused") or not state.get("stage") or state["stage"] == "done":
                    continue
                if self.active_run(identity) is not None:
                    continue
                moved = self.advance(identity) or moved
            if not moved:
                break
        for epic in self.epic_ids():
            for _ in range(3):
                before = self.epic_state(epic)["stage"]
                self.epic_step(epic)
                if self.epic_state(epic)["stage"] == before:
                    break
        self.pull()
        self.schedule_runs()
        return {"ok": True}

    # ---- new work
    def adopt_new_work(self) -> None:
        for epic in self.epic_ids():
            if self.get("epic", epic) is None:
                state = self.epic_state(epic)
                if self.items[epic].condition == "complete":
                    state["stage"] = "done"
                self.log("epic workflow v1", 0, f"epic “{self.title(epic)}” opened in "
                         f"{state['stage'].capitalize()}", "info", subject=epic, actor="kernel")
        placement = self.placement_region()
        for identity in self.card_ids():
            if self.get("item", identity) is not None:
                continue
            state = self.state(identity)
            if self.items[identity].condition == "complete":
                state["stage"] = "done"
                continue
            if placement is not None:
                self.enter_region(identity, placement)

    # ---- decisions made elsewhere
    def sync_attention(self) -> None:
        for attn_id, record in list(self.all("attn").items()):
            if record.get("kind") == "Unblock":
                continue
            try:
                fleet = self.ports.attention.get(record["fleet"])
            except LookupError:
                self.drop("attn", attn_id)
                continue
            if fleet.state != "resolved":
                continue
            answers = [decision.answer for decision in self.ports.decisions.list()
                       if decision.attention_item == record["fleet"]]
            if not answers:
                # Closed without a decision (dismissed or resolved by hand): the workflow still needs one.
                subject = record.get("item") or record.get("epic")
                record["fleet"] = self.raise_approval(attn_id, record["text"], subject, bool(record.get("epic"))).id
                self.log("kernel", 0, f"asked again: “{record['text']}” was closed without an answer, and the "
                         "workflow still waits for one", "info", subject=subject, actor="kernel")
                continue
            choice = "approve" if answers[-1].strip().lower() in APPROVALS else "back"
            self.drop("attn", attn_id)
            self.settle(record, choice, actor="user" if answers else self.actor, answered_elsewhere=True)

    def settle(self, record: dict, choice: str, *, actor: str, answered_elsewhere: bool = False) -> None:
        """Apply an answered approval: approving satisfies `you approve`; sending back moves the work back."""
        how = " (answered outside the canvas)" if answered_elsewhere else ""
        if record.get("epic"):
            epic = record["epic"]
            state = self.epic_state(epic)
            if choice == "approve":
                state["approved"] = True
                self.log("you", 0, f"accepted epic {self.title(epic)}{how}", "info", subject=epic, actor=actor)
                self.epic_step(epic)
            else:
                stages = list(self.epic_compiled().stages)
                here = stages.index(state["stage"]) if state["stage"] in stages else len(stages)
                state["stage"] = stages[max(0, here - 1)] if stages else "shape"
                state["approved"] = False
                state["held"] = self.epic_signature(epic)
                self.log("you", 0, f"sent epic {self.title(epic)} back to {state['stage'].capitalize()}{how}", "info",
                         subject=epic, actor=actor)
            return
        identity = record["item"]
        if identity not in self.items:
            return
        state = self.state(identity)
        if choice == "approve":
            state["facts"]["approved"] = True
            self.log("you", 0, f"approved {self.title(identity)} (satisfies “you approve”){how}", "info",
                     subject=identity, actor=actor)
            self.advance(identity)
        else:
            flow = self.flow(state)
            if state.get("stage") in flow:
                previous = flow[max(0, flow.index(state["stage"]) - 1)]
                self.enter_stage(identity, previous, f"sent back by you{how}")

    # ---- runs
    def sync_runs(self) -> None:
        for run in list(self.all("run").values()):
            if run["state"] not in BUSY_RUN and not (run.get("cancel") and run["state"] not in ("paused", "stopped")):
                continue
            if run.get("simulated"):
                self.sync_simulated(run)
                continue
            if not run.get("fleet_run"):
                continue
            fleet = self.ports.run(run["fleet_run"])
            if fleet is None:
                continue
            usage = getattr(fleet, "usage", None) or {}
            cost = usage.get("cost_usd") if isinstance(usage, dict) else None
            if isinstance(cost, (int, float)):
                run["cost"] = float(cost)
            status = fleet.status
            if status == "running":
                self.sync_running(run)
            elif status == "unknown outcome":
                if run["state"] == "starting" and fleet.reason not in (None, "queued"):
                    run["queue_reason"] = f"Host unreachable; outcome unknown ({fleet.reason})"
            elif status == "succeeded":
                self.finish(run, True)
            elif status == "failed" and fleet.reason == "blocked":
                self.sync_blocked(run)
            elif status == "failed":
                self.finish(run, False, fleet.reason or "failed")
            elif status == "stopped":
                if run.get("requeue"):
                    self.requeue(run)
                elif run.get("cancel"):
                    run["state"] = "paused" if run.get("pause") else "stopped"
                    run["ended_at"] = iso(self.now)
                    run["cancel"] = False
                else:
                    self.finish(run, False, fleet.reason or "stopped")

    def sync_running(self, run: dict) -> None:
        items = [item for item in self.ports.run_attention(run) if item.state != "resolved"]
        refusals = [item for item in items if item.refusals]
        others = [item for item in items if not item.refusals]
        before = run["state"]
        if others:
            run["state"] = "blocked"
            run["excerpt"] = others[0].headline
        elif refusals:
            run["state"] = "struggling"
            run["refusals"] = sum(len(item.refusals) for item in refusals)
            run["excerpt"] = refusals[0].headline
        else:
            run["state"] = "running"
            run["excerpt"] = None
        if run.get("started_at") is None:
            run["started_at"] = iso(self.now)
        if run["state"] != before and run["state"] in ("blocked", "struggling"):
            self.log(f"run {run['fleet_run'][:8]}", 0, f"{self.title(run['item'])}: {run['state']} "
                     f"({run.get('excerpt')})", "refuse", subject=run["item"], actor="kernel")
            attn_id = "perm-" + run["item"]
            if self.get("attn", attn_id) is None:
                self.put("attn", attn_id, {"id": attn_id, "kind": "Unblock", "item": run["item"], "epic": None,
                                           "text": f"Unblock {run['role']} {run.get('agent')} on "
                                                   f"{self.title(run['item'])}?",
                                           "why": f"run {run['fleet_run'][:8]} · {run.get('excerpt')}",
                                           "fleet": (refusals or others)[0].id, "run": run["id"],
                                           "time": iso(self.now)})
        if run["state"] == "running":
            self.drop("attn", "perm-" + run["item"])

    def sync_blocked(self, run: dict) -> None:
        """A step that ended `FLEET_STATUS: blocked` holds its job until someone answers it: blocked, not failed."""
        items = [item for item in self.ports.run_attention(run) if item.state != "resolved"]
        before = run["state"]
        run["state"] = "blocked"
        run["excerpt"] = items[0].headline if items else "Waiting for an answer to its question"
        if before != "blocked":
            self.log(f"run {run['fleet_run'][:8]}", 0, f"{self.title(run['item'])}: blocked ({run['excerpt']})", "refuse",
                     subject=run["item"], actor="kernel")

    def sync_simulated(self, run: dict) -> None:
        if run.get("cancel"):
            run["state"] = "paused" if run.get("pause") else "stopped"
            run["ended_at"] = iso(self.now)
            run["cancel"] = False
            return
        started = parse_time(run.get("started_at"))
        if run["state"] == "starting" or started is None:
            run["state"], run["started_at"] = "running", iso(self.now)
            return
        seconds = run.get("duration") or SIMULATED_SECONDS
        elapsed = (self.now - started).total_seconds()
        run["progress"] = min(100, int(elapsed / seconds * 100))
        run["cost"] = round(0.01 * elapsed, 2)
        if elapsed >= seconds:
            self.finish(run, True)

    def finish(self, run: dict, ok: bool, reason: str | None = None) -> None:
        identity = run["item"]
        run["state"] = "succeeded" if ok else "failed"
        run["outcome"] = "succeeded" if ok else reason
        run["ended_at"] = iso(self.now)
        run["cancel"] = False
        self.drop("attn", "perm-" + identity)
        if identity not in self.items:
            return
        state = self.state(identity)
        facts = state["facts"]
        where = f"run · {run.get('agent')}" + (" (simulated)" if run.get("simulated") else "")
        if not ok:
            self.log(where, 0, f"{run['role']} run failed on {self.title(identity)}: {reason}", "refuse",
                     subject=identity, actor="kernel")
            stage = state.get("stage")
            if stage and stage in self.all("stage") and run["role"] == "tester":
                self.run_section("stage", self.get("stage", stage), "fail", identity)
            return
        if run["role"] == "builder":
            facts["submitted"] = True
            facts["revision"] = run["id"]
            state["builder"] = run.get("agent")
            self.log(where, 0, f"revision submitted for {self.title(identity)}", "info", subject=identity,
                     actor="kernel")
        elif run["role"] == "tester":
            stage = state.get("stage")
            checks = []
            if stage and stage in self.all("stage"):
                checks = [line.args[0] for line in self.compiled("stage", stage).ops("when") if line.op == "evidence"]
            for check in checks or ["tests pass"]:
                facts.setdefault("evidence", {})[check] = facts.get("revision") or run["id"]
            self.log("kernel", 0, f"evidence captured: {', '.join(checks or ['tests pass'])} on the current revision "
                     f"of {self.title(identity)}", "info", subject=identity, actor="kernel")

    def enforce_budgets(self) -> None:
        for identity in self.card_ids():
            state = self.get("item", identity)
            if not state or state.get("budget") is None or state.get("paused"):
                continue
            spent = sum(run.get("cost") or 0 for run in self.runs_for(identity, active=False))
            if spent >= state["budget"] and self.active_run(identity) is not None and state["budget"] > 0:
                state["paused"], state["paused_by"] = True, "budget"
                self.stop_runs(identity, "Budget spent", pause=True)
                self.log("kernel", 0, f"{self.title(identity)} spent ${spent:.2f} of a ${state['budget']:.0f} "
                         "budget; paused", "refuse", subject=identity, actor="kernel")

    # ---- regions that pull
    def pull(self) -> None:
        regions = self.all("region")
        for region in list(regions.values()):
            if region.get("level") != "enforced":
                continue
            line = next((line for line in self.compiled("region", region["id"]).ops("agents") if line.op == "pull"), None)
            if line is None:
                continue
            source = next((other for other in regions.values()
                           if other["name"].lower() == line.args[0].lower() and other["id"] != region["id"]), None)
            if source is None:
                continue
            candidates = sorted((identity for identity in self.card_ids() if self.state(identity).get("region") == source["id"]),
                                key=lambda identity: self.state(identity).get("parked_at") or "")
            if not candidates:
                continue
            try:
                self.guard_region(region, candidates[0])
            except Exception:
                continue
            identity = candidates[0]
            self.log(self.label("region", region), line.n, f"pull: {region['name']} has room, so the orchestrator "
                     f"moved “{self.title(identity)}” from {source['name']}", "op", subject=identity, actor="orchestrator")
            self.leave_region(identity)
            self.enter_region(identity, region)

    # ---- scheduler
    def agents(self) -> dict[str, dict]:
        settings = self.get("settings", "main") or {}
        return settings.get("agents", {})

    def schedule_runs(self) -> list[dict]:
        compiled = self.schedule()
        policy = compiled.options
        record = self.get("schedule", "main") or {"version": 1}
        src = f"schedule v{record['version']}"
        capacity = policy["capacity"]
        configured = self.agents()
        busy = {agent: 0 for agent in capacity}
        for run in self.all("run").values():
            if run["state"] in BUSY_RUN and run.get("agent") in busy:
                busy[run["agent"]] += 1
        epicflow = self.epic_compiled()
        gated = any(line.op == "dispatch_children" for line in epicflow.ops("enter"))
        queue = sorted((run for run in self.all("run").values() if run["state"] == "queued"),
                       key=lambda run: (RANK.get(self.state(run["item"]).get("band") or "later", 2),
                                        PRIORITY_RANK.get(self.state(run["item"]).get("priority"), 1),
                                        run["queued_at"]))
        started = []
        for run in queue:
            identity = run["item"]
            state = self.state(identity)
            if identity not in self.items:
                run["state"] = "stopped"
                continue
            if state.get("paused"):
                run["queue_reason"] = "Paused"
                continue
            retry_at = run.get("retry_at")
            if retry_at and parse_time(retry_at) > self.now:
                continue
            epic = self.epic_of(identity)
            if gated and epic and self.epic_state(epic)["stage"] == "shape":
                run["queue_reason"] = f"Epic {self.title(epic)} is still in Shape; its children start in Deliver"
                continue
            if policy["dependencies"]:
                open_deps = self.waits_on(identity)
                if open_deps:
                    run["queue_reason"] = "Waiting on " + ", ".join(self.title(other) for other in open_deps)
                    continue
            agent, reason = self.pick_agent(run, capacity, busy, configured, policy)
            if agent is None:
                run["queue_reason"] = reason
                continue
            busy[agent] += 1
            self.assign(run, agent, busy[agent], capacity[agent], src, policy["lines"].get("order", 0))
            started.append(run)
        return started

    def pick_agent(self, run: dict, capacity: dict, busy: dict, configured: dict, policy: dict):
        builder = run.get("builder") or self.state(run["item"]).get("builder")
        independent = run["role"] == "tester" and (policy["independent_testers"] or run.get("independent"))
        if run.get("force_agent"):
            candidates = [run["force_agent"]]
        elif run["role"] == "tester" and independent:
            candidates = [agent for agent in capacity if agent != builder]
        elif run["role"] == "builder" and builder in capacity:
            candidates = [builder] + [agent for agent in capacity if agent != builder]
        else:
            candidates = list(capacity)
        candidates = [agent for agent in candidates if agent in capacity]
        if not candidates:
            if independent:
                return None, f"Queued: needs an agent other than {builder} to test independently; the schedule has none"
            return None, "Queued: the schedule gives no agent capacity for this run"
        ready = [agent for agent in candidates if agent in configured]
        if not ready:
            names = " or ".join(candidates)
            return None, (f"Queued: no host is set up for {names}. Set one with "
                          f"fleet canvas agent {self.space} {candidates[0]} --host HOST --cwd DIR")
        free = [agent for agent in ready if busy.get(agent, 0) < capacity[agent]]
        if not free:
            if independent:
                return None, (f"Queued: needs {' or '.join(ready)} to test independently of {builder}; no free slot")
            return None, "Queued: " + ", ".join(f"{agent} {busy.get(agent, 0)}/{capacity[agent]}" for agent in ready) + " busy"
        return free[0], None

    def assign(self, run: dict, agent: str, slot: int, cap: int, src: str, line: int) -> None:
        settings = self.agents().get(agent, {})
        run.update(agent=agent, queue_reason="", error=None, started_at=None, retry_at=None)
        state = self.state(run["item"])
        if run["role"] == "builder":
            state["builder"] = agent
        if settings.get("mode") == "simulate":
            run.update(state="running", simulated=True, started_at=iso(self.now),
                       duration=int(settings.get("seconds") or SIMULATED_SECONDS))
        else:
            run.update(state="starting", dispatch=True, queue_reason="Starting on its host")
        self.log(src, line, f"started {run['role']} {agent} on {self.title(run['item'])} ({agent} slot {slot} of {cap}, "
                 f"band {state.get('band') or 'later'})" + (" · simulated" if run.get("simulated") else ""), "op",
                 subject=run["item"], actor="scheduler")

    def dispatch_failed(self, run_id: str, error: str) -> None:
        run = self.get("run", run_id)
        if run is None:
            return
        run.update(state="queued", dispatch=False, error=error, agent=run.get("force_agent"),
                   retry_at=iso(self.now + timedelta(seconds=RETRY_SECONDS)),
                   queue_reason=f"Could not start on its host: {error}. Retrying in a minute")
        self.log("scheduler", 0, f"could not start {run['role']} for {self.title(run['item'])}: {error}", "refuse",
                 subject=run["item"], actor="scheduler")

    def dispatched(self, run_id: str, fleet_run: str, host: str, job: str) -> None:
        run = self.get("run", run_id)
        if run is None:
            return
        run.update(dispatch=False, fleet_run=fleet_run, host=host, job=job, error=None)
        if run["state"] != "starting":
            # Paused, stopped or reassigned while the start was on its way: cancel what just started.
            run.update(cancel=True, cancel_sent=False)
            return
        run["queue_reason"] = "Starting on " + host

    def active_states(self) -> tuple[str, ...]:
        return ACTIVE_RUN
