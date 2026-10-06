"""Run canvas operations against the store, and carry the kernel's run outbox to hosts.

Every client (deck canvas, reader, CLI, agents) calls `operation`. It runs the
kernel in one controller transaction; a refusal rolls that transaction back and
is written to the event log on its own. The host side (starting, cancelling and
permitting real agent runs) only happens in `tick`, outside any transaction, and
its outcome is recorded by a second kernel step.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Callable

from fleet.api import DispatchRequest
from fleet.modules.canvas import CanvasFacade, Ports, Refused, defaults
from fleet.transport import FleetError

Scope = Callable[[], AbstractContextManager[tuple[Any, CanvasFacade]]]
INTERNAL = "scheduler"


class CanvasService:
    def __init__(self, scope: Scope, *, reader: Callable[[], CanvasFacade], workspace: Callable[[], Any], dispatch: Callable[[], Any],
                 jobs: Callable[[], Any], execution: Callable[[], Any], transport: Any) -> None:
        self.scope, self.workspace, self.dispatch, self.jobs, self.transport = scope, workspace, dispatch, jobs, transport
        self.execution, self.reader = execution, reader

    # ---- wiring
    def ports(self, facades, space: str) -> Ports:
        execution, attention = facades.execution, facades.attention

        def run(identity: str):
            try:
                return execution.get_run(identity)
            except LookupError:
                return None

        def run_attention(identity: str) -> list:
            return [item for item in attention.list(project=space) if item.run == identity]

        return Ports(work=facades.work, attention=attention, decisions=facades.decisions, run=run,
                     run_attention=run_attention)

    def project(self, reference: str) -> tuple[str, str]:
        workspace = self.workspace()
        try:
            identity = workspace.resolve_project(reference)
        except FleetError:
            raise
        project = workspace.registry().projects.get(identity)
        return identity, project.name if project is not None else identity

    # ---- operations
    def operation(self, space: str, op: str, args: dict | None = None, *, actor: str,
                  op_id: str | None = None) -> dict:
        """The one write path. Returns the operation's result, or the refusal in the contract's shape."""
        space, _ = self.project(space)
        args = args or {}
        try:
            with self.scope() as (facades, canvas):
                return canvas.execute(space, self.ports(facades, space), actor=actor, op=op, args=args, op_id=op_id)
        except Refused as refusal:
            return self.refused(space, refusal, actor=actor, op=op, op_id=op_id)
        except (ValueError, LookupError) as error:
            return self.refused(space, Refused("invalid", str(error)), actor=actor, op=op, op_id=op_id)

    def refused(self, space: str, refusal: Refused, *, actor: str, op: str, op_id: str | None) -> dict:
        with self.scope() as (_, canvas):
            if op_id is not None:
                previous = canvas.repository.op_result(op_id)
                if previous is not None:
                    return previous | {"replayed": True}
            return canvas.refusal(space, refusal, actor=actor, op=op, op_id=op_id)

    def layout(self, space: str, person: str, object_ref: str, props: dict | None) -> dict:
        """Personal arrangement: last write wins, unversioned and unlogged."""
        space, _ = self.project(space)
        if not isinstance(object_ref, str) or not object_ref or len(object_ref) > 200:
            raise ValueError("object is a reference such as task:<id> or doc:spec")
        if props is not None and (not isinstance(props, dict) or len(str(props)) > 2000):
            raise ValueError("props is a small object of layout values")
        with self.scope() as (_, canvas):
            canvas.set_layout(space, person, object_ref, props)
        return {"ok": True}

    def init(self, space: str, *, actor: str, north_star: str | None = None, example: bool = False,
             seconds: int = 45) -> dict:
        identity, name = self.project(space)
        result = self.operation(identity, "space.init", {"name": name, "north_star": north_star}, actor=actor)
        if result.get("refused"):
            return result
        if example:
            result["example"] = self.seed_example(identity, actor=actor, seconds=seconds)
        return result

    # ---- reads
    def state(self, space: str, *, person: str) -> dict:
        space, name = self.project(space)
        with self.scope() as (facades, canvas):
            other = [item for item in facades.attention.list(project=space) if item.state != "resolved"]
            return canvas.read(space, self.ports(facades, space), person=person, project_name=name,
                               other_attention=other)

    def spaces(self) -> list[dict]:
        identities = self.reader().spaces()
        registry = self.workspace().registry().projects
        return [{"id": identity, "name": registry[identity].name if identity in registry else identity}
                for identity in identities]

    def events(self, space: str, *, after: int = 0, limit: int | None = None) -> list[dict]:
        space, _ = self.project(space)
        return self.reader().events(space, after=after, limit=limit)

    def last_seq(self, space: str) -> int:
        space, _ = self.project(space)
        return self.reader().repository.last_event(space)

    def versions(self, space: str, kind: str, identity: str) -> list[dict]:
        space, _ = self.project(space)
        return self.reader().versions(space, kind, identity)

    def compile_preview(self, text: str, level: str = "enforced") -> dict:
        from fleet.modules.canvas import CodeInvalid, compile_code
        try:
            return compile_code(text, level=level).as_dict()
        except CodeInvalid as error:
            return {"error": str(error)}

    # ---- the tick and the host side
    def tick_all(self) -> list[dict]:
        return [self.tick(space) for space in self.reader().spaces()]

    def tick(self, space: str) -> dict:
        result = self.operation(space, "tick", {}, actor="kernel")
        sent = self.carry_outbox(space)
        return {"space": space, "result": result, "outbox": sent}

    def carry_outbox(self, space: str) -> list[dict]:
        outbox = self.reader().outbox(space)
        done = []
        for run in outbox:
            if run.get("dispatch") and not run.get("fleet_run"):
                done.append(self.start(space, run))
            elif run.get("cancel") and run.get("fleet_run") and not run.get("cancel_sent"):
                done.append(self.cancel(space, run))
            elif run.get("permit") and not run.get("permitted"):
                done.append(self.permit(space, run))
        return [entry for entry in done if entry]

    def kernel_step(self, space: str, change: Callable[[Any], None]) -> None:
        with self.scope() as (facades, canvas):
            engine = canvas.engine(space, self.ports(facades, space), actor=INTERNAL)
            change(engine)
            canvas.commit(space, engine)

    def start(self, space: str, run: dict) -> dict:
        try:
            with self.scope() as (facades, canvas):
                engine = canvas.engine(space, self.ports(facades, space), actor=INTERNAL)
                settings = engine.agents().get(run.get("agent") or "", {})
                brief = engine.brief(run["id"])
                title = engine.title(run["item"])
                allow = list((engine.get("settings", "main") or {}).get("allow", []))
            if settings.get("mode") != "dispatch":
                raise FleetError(f"no host is set up for {run.get('agent')}")
            request = DispatchRequest(
                host=settings["host"], project=space, description=f"{run['role']}: {title}"[:200],
                agent=settings.get("runtime") or run["agent"], cwd=settings["cwd"], work_item=run["item"],
                permission=settings.get("permission"), model=settings.get("model"), id=run["id"],
                allow=allow or None, add_dir=None, env=None, keep_going=False, context=None, hold=False,
                actor=INTERNAL)
            sent = self.dispatch().send(request, [{"prompt": brief}])
            current = sent["intent"].run
        except (FleetError, ValueError, LookupError, OSError, RuntimeError) as error:
            message = str(error).strip().splitlines()[0][:300] if str(error).strip() else type(error).__name__
            self.kernel_step(space, lambda engine: engine.dispatch_failed(run["id"], message))
            return {"run": run["id"], "started": False, "error": message}
        self.kernel_step(space, lambda engine: engine.dispatched(run["id"], current.id, current.host,
                                                                 current.remote_job_id))
        return {"run": run["id"], "started": True, "fleet_run": current.id}

    def cancel(self, space: str, run: dict) -> dict:
        try:
            host = self.transport.host_by_name(run["host"])
            self.jobs().cancel(host, run["job"], all_steps=True)
            error = None
        except (FleetError, OSError, LookupError, RuntimeError) as failure:
            error = str(failure)[:300]

        def mark(engine):
            record = engine.get("run", run["id"])
            if record is not None:
                record["cancel_sent"] = error is None
                record["cancel_error"] = error
        self.kernel_step(space, mark)
        return {"run": run["id"], "cancelled": error is None, "error": error}

    def permit(self, space: str, run: dict) -> dict:
        rules: list[str] = []
        error = None
        item_id = run.get("permit_item")
        try:
            with self.scope() as (facades, _):
                if item_id:
                    item = facades.attention.get(item_id)
                    rules = sorted({rule for refusal in item.refusals for rule in (refusal.rules or ())})
            if item_id:
                self.grant(item_id)
        except (FleetError, ValueError, LookupError, RuntimeError, OSError) as failure:
            error = str(failure)[:300]

        def mark(engine):
            record = engine.get("run", run["id"])
            if record is not None:
                record["permitted"] = True
                record["permit_error"] = error
            if run.get("permit") in ("space", "everywhere") and rules:
                settings = engine.get("settings", "main") or engine.put("settings", "main",
                                                                         {"id": "main", "agents": {}, "allow": []})
                settings["allow"] = sorted(set(settings.get("allow", [])) | set(rules))
                engine.log("you", 0, "future runs here are allowed: " + ", ".join(rules), "op", subject=run["item"])
        self.kernel_step(space, mark)
        return {"run": run["id"], "permitted": error is None, "rules": rules, "error": error}

    def grant(self, item_id: str) -> None:
        self.execution().grant_permissions(item_id, "refused", actor="user")

    # ---- an example space
    def seed_example(self, space: str, *, actor: str, seconds: int = 45) -> dict:
        """The embeddings-service example the canvas was designed around, on simulated agents."""
        ops = []

        def run(op, args):
            result = self.operation(space, op, args, actor=actor)
            if result.get("refused"):
                raise FleetError(f"seeding the example failed at {op}: {result['message']}")
            ops.append(op)
            return result["result"]

        run("charter.update", {"patch": {
            "north_star": "Move embeddings into a separate service without making matching worse or the webapp heavier."}})
        for clause in (
                {"text": "Matching results change only with your review", "kind": "enforced", "rule": "Matching results need you"},
                {"text": "The webapp keeps pgvector; the service only turns text into vectors", "kind": "enforced",
                 "rule": "Architecture drift"},
                {"text": "Deleting images or caches needs you", "kind": "enforced", "rule": "Deleting images needs you"},
                {"text": "No cold rebuilds; reuse warm images unless you say otherwise", "kind": "guidance"},
                {"text": "Phase 1 stays on the internal network; TLS and tokens come in phase 2", "kind": "guidance"}):
            run("charter.update", {"patch": {"add_clause": clause}})
        run("spec.update", {"text": "Move text-to-vector embedding out of the webapp into its own service. The webapp "
                                    "keeps pgvector, matching stays hybrid, and phase 1 runs on the internal network."})
        phase1 = run("epic.create", {"title": "Phase 1: embeddings as a service", "criteria": [
            "p95 latency at most 20% worse than the baseline", "Matching results unchanged unless you ratify",
            "The full diff reviewed independently", "The webapp image ships without torch"]})["epic"]
        evaluation = run("epic.create", {"title": "Embedder evaluation", "criteria": [
            "A candidate matches current recall within 2%", "Cost per 1,000 embeddings compared"]})["epic"]
        state = self.state(space, person=actor)
        epic_criteria = {epic["id"]: [criterion["id"] for criterion in epic["criteria"]] for epic in state["epics"]}
        tasks = [
            ("M1b: matching facade", phase1, ["Characterization tests pass on this revision",
                                              "You ratify any change to matching results"], [1], "now"),
            ("Deploy baseline timings", phase1, ["Baseline p95 recorded", "Parity check against carbon"], [0], "next"),
            ("Final review K2", phase1, ["Fix commits re-tested"], [2], "later"),
            ("Evaluate a Hugging Face embedder", evaluation, [], [0], "next"),
            ("Lazy embedder imports", phase1, ["The embedder is imported only where vectors are made"], [3], "now"),
            ("Embeddings client in requirements", phase1, ["Client installed from requirements, non-editable"], [], None),
        ]
        made = {}
        for title, epic, criteria, covers, band in tasks:
            identity = run("item.create", {"title": title, "epic": epic, "criteria": criteria,
                                           "covers": [epic_criteria[epic][index] for index in covers],
                                           "stage": "first" if band else None})["item"]
            made[title] = identity
            if band and band != "later":
                run("item.set_band", {"item": identity, "band": band})
        run("dep.add", {"from": made["Deploy baseline timings"], "to": made["Final review K2"]})
        for agent in ("claude", "codex"):
            run("agent.configure", {"agent": agent, "mode": "simulate", "seconds": seconds})
        return {"operations": len(ops), "items": made, "epics": [phase1, evaluation]}
