"""The canvas kernel: every canvas, reader, CLI or agent gesture is one operation here.

The kernel reads a space (its canvas records plus the project's work items, runs
and attention), applies one operation or one tick, and leaves behind changed
records, version snapshots and event-log entries for the caller to persist in
the same transaction. It never contacts a host: starting, cancelling or
permitting a real run is left as an outbox on the run record for the service.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

from ..domain import defaults
from ..domain.language import (Compiled, compile_code, compile_schedule)
from .ports import Ports

STATUSES = ("working", "idle", "waiting-you", "waiting-criteria", "paused", "done", "struggling", "blocked", "queued")
ACTIVE_RUN = ("queued", "starting", "running", "struggling", "blocked")
BUSY_RUN = ("starting", "running", "struggling", "blocked")
# A run's outbox: what the service still has to do on its host. A requeued run starts with none of it done.
FRESH_OUTBOX = {"dispatch": False, "cancel": False, "cancel_sent": False, "cancel_error": None, "pause": False,
                "permit": None, "permitted": False, "permit_error": None, "permit_item": None, "error": None,
                "retry_at": None}
# Readings that change while a run works; they are observations, not state changes, so they leave no history.
OBSERVED_RUN_FIELDS = ("progress", "cost", "excerpt", "refusals")
ROLES = ("builder", "tester", "decomposer")
EPIC_COLORS = ("#8fb7e6", "#e3a76f", "#b5d36a", "#d47fa6", "#9b86d1", "#d9c25a", "#6cc9ad", "#e07a6a")
ZONE_COLORS = ("#5d8f84", "#6a9cc4", "#9b86d1", "#d47fa6", "#e3a76f", "#d9c25a", "#7fb069", "#e07a6a", "#8a979b")
PEOPLE = ("user", "web-user")
REFUSAL_CODES = ("exit_condition_unmet", "stage_skipped", "capacity_full", "wrong_kind", "cycle",
                 "rule_outranks_scope", "version_conflict", "not_permitted", "not_found", "invalid")


class Refused(Exception):
    """A kernel refusal, naming the code that refused it."""

    def __init__(self, code: str, message: str, *, source: dict | None = None, condition: str | None = None,
                 current: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.source, self.condition, self.current = code, message, source, condition, current

    def as_dict(self, op_id: str | None = None) -> dict:
        body = {"refused": True, "code": self.code, "message": self.message, "source": self.source,
                "op_id": op_id}
        if self.condition is not None:
            body["condition"] = self.condition
        if self.current is not None:
            body["current"] = self.current
        return body


def is_person(actor: str) -> bool:
    return actor in PEOPLE or actor.startswith("user:")


def iso(moment: datetime) -> str:
    return moment.isoformat()


def parse_time(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value)


def strip(run: dict) -> dict:
    return {key: value for key, value in run.items() if key not in OBSERVED_RUN_FIELDS}


def short(text: str, words: int = 12) -> str:
    parts = str(text).split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


class Kernel:
    """One space, loaded for one operation or tick."""

    def __init__(self, space: str, records: dict[str, dict[str, dict]], ports: Ports, *, now: datetime,
                 actor: str, op_id: str | None = None) -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise Refused("invalid", "every write names an actor")
        self.space, self.ports, self.now, self.actor, self.op_id = space, ports, now, actor, op_id
        self.records = {kind: dict(values) for kind, values in records.items()}
        self.original = deepcopy(self.records)
        self.events: list[dict] = []
        self.versions: list[tuple[str, str, int, dict]] = []
        self.presentation: set[tuple[str, str]] = set()
        self.toasts: list[str] = []
        self._compiled: dict = {}
        self.load_work()

    # ---- records
    def all(self, kind: str) -> dict[str, dict]:
        return self.records.setdefault(kind, {})

    def get(self, kind: str, identity: str) -> dict | None:
        return self.records.get(kind, {}).get(identity)

    def need(self, kind: str, identity: str, label: str | None = None) -> dict:
        record = self.get(kind, identity)
        if record is None:
            raise Refused("not_found", f"no {label or kind} '{identity}' in this space")
        return record

    def put(self, kind: str, identity: str, record: dict) -> dict:
        self.all(kind)[identity] = record
        self._compiled.pop((kind, identity), None)
        return record

    def drop(self, kind: str, identity: str) -> None:
        self.all(kind).pop(identity, None)

    def changes(self) -> list[tuple[str, str, dict | None, bool]]:
        """Records that differ from what was loaded: (kind, id, record or None, presentation only)."""
        found = []
        for kind in set(self.records) | set(self.original):
            now, before = self.records.get(kind, {}), self.original.get(kind, {})
            for identity in set(now) | set(before):
                if now.get(identity) != before.get(identity):
                    observed = (kind == "run" and now.get(identity) is not None and before.get(identity) is not None
                                and strip(now[identity]) == strip(before[identity]))
                    found.append((kind, identity, now.get(identity),
                                  observed or (kind, identity) in self.presentation))
        return sorted(found, key=lambda change: (change[0], change[1]))

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid4().hex[:10]}"

    # ---- work snapshot
    def load_work(self) -> None:
        work = self.ports.work
        self.items = {item.id: item for item in work.list(project=self.space)}
        criteria = work.criteria_by_item()
        self.criteria = {identity: list(criteria.get(identity, [])) for identity in self.items}
        relations = work.relations_by_item()
        self.relations = [relation for identity in self.items for relation in relations.get(identity, [])
                          if relation.type == "depends-on" and relation.to_item in self.items]

    def card_ids(self) -> list[str]:
        return [identity for identity, item in self.items.items()
                if item.kind != "epic" and item.condition != "dropped"]

    def epic_ids(self) -> list[str]:
        return [identity for identity, item in self.items.items() if item.kind == "epic" and item.condition != "dropped"]

    def epic_of(self, identity: str) -> str | None:
        item = self.items.get(identity)
        seen = set()
        while item is not None and item.parent and item.parent not in seen:
            seen.add(item.parent)
            item = self.items.get(item.parent)
            if item is not None and item.kind == "epic":
                return item.id
        return None

    def title(self, identity: str) -> str:
        item = self.items.get(identity)
        return item.title if item is not None else identity

    def require_card(self, identity: str) -> dict:
        identity = self.resolve_item(identity)
        if identity not in self.items or self.items[identity].kind == "epic":
            raise Refused("not_found", f"no task '{identity}' in this space")
        return self.state(identity)

    def resolve_item(self, reference: str) -> str:
        if not isinstance(reference, str) or not reference:
            raise Refused("invalid", "a work item is required")
        if reference in self.items:
            return reference
        matches = [identity for identity in self.items if identity.startswith(reference)]
        if len(matches) == 1:
            return matches[0]
        raise Refused("not_found", f"no single work item matches '{reference}'")

    # ---- per-item canvas state
    def state(self, identity: str) -> dict:
        record = self.get("item", identity)
        if record is None:
            record = self.put("item", identity, {
                "id": identity, "stage": None, "band": "later", "region": None, "owner": "unassigned",
                "budget": None, "priority": "normal", "paused": False, "paused_by": None, "parked_at": None,
                "pin": None, "covers": [], "builder": None,
                "facts": {"submitted": False, "revision": None, "evidence": {}, "approved": False},
                "seen_at": iso(self.now)})
        return record

    def epic_state(self, identity: str) -> dict:
        record = self.get("epic", identity)
        if record is None:
            index = len(self.all("epic"))
            record = self.put("epic", identity, {"id": identity, "stage": "shape",
                                                 "color": EPIC_COLORS[index % len(EPIC_COLORS)],
                                                 "ref": identity[:6], "approved": False})
        return record

    # ---- code objects
    def code_record(self, kind: str, identity: str) -> dict:
        record = self.get(kind, identity)
        if record is None:
            raise Refused("not_found", f"no {kind} '{identity}' in this space")
        return record

    def compiled(self, kind: str, identity: str) -> Compiled:
        record = self.code_record(kind, identity)
        key = (kind, identity)
        cached = self._compiled.get(key)
        if cached is not None and cached[0] == (record.get("version"), record.get("code"), record.get("level")):
            return cached[1]
        level = record.get("level", "enforced") if kind == "region" else "enforced"
        compiled = compile_code(record["code"], level=level)
        self._compiled[key] = ((record.get("version"), record.get("code"), record.get("level")), compiled)
        return compiled

    def label(self, kind: str, record: dict) -> str:
        if kind == "stage":
            return f"stage {record['id']} v{record['version']}"
        if kind == "region":
            return f"zone {record['name']} v{record['version']}"
        if kind == "schedule":
            return f"schedule v{record['version']}"
        if kind == "epicflow":
            return f"epic workflow v{record['version']}"
        if kind == "view":
            return f"view {record['type']} v{record['version']}"
        if kind == "workflow":
            return f"workflow v{record['version']}"
        return kind

    def source(self, kind: str, record: dict, line: int = 0) -> dict:
        name = {"stage": lambda: f"stage {record['id']}", "region": lambda: f"zone {record['name']}",
                "schedule": lambda: "schedule", "epicflow": lambda: "epic workflow",
                "view": lambda: f"view {record['type']}", "workflow": lambda: "workflow"}[kind]()
        return {"object": name, "version": record["version"], "line": line}

    def workflow(self) -> dict:
        return self.need("workflow", "main", "workflow")

    def schedule(self) -> Compiled:
        record = self.get("schedule", "main")
        return compile_schedule(record["code"] if record else defaults.SCHEDULE)

    def flow(self, state: dict) -> list[str]:
        """The stages an item moves through: its pinned workflow version's, or the current one's."""
        workflow = self.workflow()
        stages = workflow["stages"]
        if state.get("pin"):
            for entry in workflow.get("history", []):
                if entry["version"] == state["pin"]:
                    stages = [stage for stage in entry["stages"] if stage in self.all("stage")]
                    break
        return list(stages) + ["done"]

    def stage_name(self, stage: str | None) -> str:
        if stage == "done":
            return "Done"
        if stage is None:
            return "no stage"
        record = self.get("stage", stage)
        return record["name"] if record else stage

    # ---- event log
    def log(self, source: str, line: int, text: str, tone: str = "info", *, subject: str | None = None,
            actor: str | None = None) -> None:
        self.events.append({"time": iso(self.now), "actor": actor or self.actor, "source": source, "line": line,
                            "text": text, "tone": tone, "subject": subject, "op_id": self.op_id})

    def refuse(self, code: str, message: str, *, kind: str | None = None, record: dict | None = None,
               line: int = 0, condition: str | None = None, subject: str | None = None) -> Refused:
        source = self.source(kind, record, line) if kind and record else None
        return Refused(code, message, source=source, condition=condition)

    def snapshot(self, kind: str, identity: str) -> None:
        record = self.get(kind, identity)
        if record is not None:
            self.versions.append((kind, identity, record["version"], deepcopy(record)))

    # ---- facts and exit conditions
    def has_criteria(self, identity: str) -> bool:
        return bool(self.criteria.get(identity))

    def when_ok(self, identity: str) -> dict:
        state = self.state(identity)
        stage = state.get("stage")
        if not stage or stage == "done" or stage not in self.all("stage"):
            return {"ok": False}
        record = self.get("stage", stage)
        compiled = self.compiled("stage", stage)
        facts = state["facts"]
        for line in compiled.ops("when"):
            if line.op == "has_criteria":
                ok = self.has_criteria(identity)
            elif line.op == "submitted":
                ok = bool(facts.get("submitted"))
            elif line.op == "evidence":
                ok = bool(facts.get("revision")) and facts.get("evidence", {}).get(line.args[0]) == facts.get("revision")
            elif line.op == "approved":
                ok = bool(facts.get("approved"))
            else:
                ok = True
            if not ok:
                return {"ok": False, "line": line.n, "text": line.text, "record": record,
                        "src": self.label("stage", record)}
        return {"ok": True, "record": record, "src": self.label("stage", record),
                "lines": [line.n for line in compiled.ops("when")]}

    # ---- run requests
    def runs_for(self, identity: str, *, active: bool = True) -> list[dict]:
        return [run for run in self.all("run").values() if run["item"] == identity
                and (not active or run["state"] in ACTIVE_RUN)]

    def active_run(self, identity: str) -> dict | None:
        runs = sorted(self.runs_for(identity), key=lambda run: run["queued_at"])
        return runs[-1] if runs else None

    def request_run(self, identity: str, role: str, source: str, line: int) -> dict:
        if role not in ROLES:
            raise Refused("invalid", f"unknown role '{role}'; use builder, tester or decomposer")
        existing = [run for run in self.runs_for(identity) if run["role"] == role]
        if existing:
            return existing[0]
        state = self.state(identity)
        run_id = self.new_id("run")
        run = self.put("run", run_id, {
            "id": run_id, "item": identity, "role": role, "state": "queued", "agent": None,
            "builder": state.get("builder"), "queued_at": iso(self.now), "queue_reason": "Waiting for the scheduler",
            "requested_by": source, "fleet_run": None, "host": None, "job": None, "dispatch": False,
            "cancel": False, "permit": None, "error": None, "retry_at": None, "started_at": None,
            "ended_at": None, "outcome": None, "simulated": False, "revision": state["facts"].get("revision"),
            "force_agent": None, "independent": False, "attempt": 1})
        verb = {"builder": "dispatch builder", "tester": "dispatch tester", "decomposer": "dispatch decomposer"}[role]
        extra = f" (must be independent of builder {state.get('builder')})" if role == "tester" and state.get("builder") else ""
        self.log(source, line, f"{verb} requested for {self.title(identity)} (queued for the scheduler){extra}", "op",
                 subject=identity)
        return run

    def stop_runs(self, identity: str, why: str, *, pause: bool) -> None:
        for run in self.runs_for(identity):
            if run["state"] == "queued" or (run["state"] == "starting" and not run.get("fleet_run")):
                # Nothing is on a host yet, so nothing needs cancelling there.
                run.update(state="paused" if pause else "stopped", ended_at=iso(self.now), dispatch=False)
            else:
                run["cancel"] = True
                run["pause"] = pause
                run["queue_reason"] = why

    # ---- running code
    def run_section(self, kind: str, record: dict, section: str, identity: str, *, stage: str | None = None) -> None:
        if kind == "region" and record.get("level") == "label":
            return
        compiled = self.compiled(kind, record["id"]) if kind != "epicflow" else self.epic_compiled()
        src = self.label(kind, record)
        for line in compiled.guidance(section, stage=stage):
            self.log(src, line.n, f"guidance passed to the owning agent: “{line.text}” for {self.title(identity)}",
                     "guide", subject=identity)
        for line in compiled.ops(section, stage=stage):
            self.apply_line(kind, record, line, identity)

    def apply_line(self, kind: str, record: dict, line, identity: str) -> None:
        src = self.label(kind, record)
        title = self.title(identity)
        arg = (line.args[0] if line.args else "").replace("{item}", title)
        is_epic = identity in self.items and self.items[identity].kind == "epic"
        state = self.epic_state(identity) if is_epic else self.state(identity)
        op = line.op
        if op == "assign":
            state["owner"] = arg
            self.log(src, line.n, f"assign owner {arg} → {title}", "op", subject=identity)
        elif op == "dispatch_builder":
            self.request_run(identity, "builder", src, line.n)
        elif op in ("dispatch_tester", "dispatch_tester_any"):
            run = self.request_run(identity, "tester", src, line.n)
            run["independent"] = op == "dispatch_tester"
        elif op == "dispatch_decomposer":
            self.log(src, line.n, f"asked for child tasks covering the uncovered criteria of {title}", "op",
                     subject=identity)
            self.decompose(identity, author="orchestrator")
        elif op == "dispatch_children":
            self.log(src, line.n, f"children of {title} may now be dispatched", "op", subject=identity)
        elif op == "start_if_not_started":
            if not is_epic and not state.get("stage"):
                self.log(src, line.n, f"start if not started → {title}", "op", subject=identity)
                self.enter_stage(identity, self.flow(state)[0], f"started by {src}")
        elif op == "ask":
            self.ask(identity, arg, src, line.n, kind=kind, record=record)
        elif op == "notify":
            note_id = self.new_id("note")
            self.put("note", note_id, {"id": note_id, "text": arg, "why": f"{src} · line {line.n}",
                                       "time": iso(self.now), "subject": identity})
            self.log(src, line.n, f"notify you: {arg}", "op", subject=identity)
        elif op == "pause":
            if not state.get("paused"):
                state["paused"], state["paused_by"] = True, src
                self.stop_runs(identity, f"Paused by {src}", pause=True)
            self.log(src, line.n, f"pause runs → {title}", "op", subject=identity)
        elif op == "resume":
            self.resume(identity, src, line.n)
        elif op == "budget":
            state["budget"] = float(line.args[0])
            self.log(src, line.n, f"set budget ${line.args[0]} → {title}", "op", subject=identity)
        elif op == "priority":
            state["priority"] = line.args[0]
            self.log(src, line.n, f"set priority {line.args[0]} → {title}", "op", subject=identity)
        elif op == "replan_after":
            parked = parse_time(state.get("parked_at"))
            days = int(line.args[0])
            held = (self.now - parked) if parked else timedelta(0)
            if held > timedelta(days=days):
                self.log(src, line.n, f"{title} was parked {held.days} days, over {days}: re-plan required", "op",
                         subject=identity)
                state["facts"]["submitted"] = False
                self.enter_stage(identity, self.flow(state)[0], f"re-plan required by {src}")
            else:
                hours = int(held.total_seconds() // 3600)
                self.log(src, line.n, f"check: parked {hours}h, under {days} days ✓ no re-plan needed", "op",
                         subject=identity)
        elif op == "send_back":
            target = line.args[0]
            if target in self.flow(state):
                self.enter_stage(identity, target, f"sent back by {src}")
            else:
                self.log(src, line.n, f"cannot send {title} back to {target}: no such stage", "refuse", subject=identity)
        elif op == "context_add":
            pass  # documents enter context through context.add; tasks are refused by capacity
        else:
            self.log(src, line.n, f"{line.text} → {title}", "op", subject=identity)

    def ask(self, identity: str, question: str, src: str, line: int, *, kind: str, record: dict) -> None:
        is_epic = self.items[identity].kind == "epic"
        attn_id = ("ep-" if is_epic else "ap-") + identity
        if self.get("attn", attn_id) is None:
            stage = record.get("id") if kind == "stage" else None
            fleet_item = self.raise_approval(attn_id, question, identity, is_epic)
            self.put("attn", attn_id, {"id": attn_id, "kind": "Accept" if is_epic else "Approve", "text": question,
                                       "item": None if is_epic else identity, "epic": identity if is_epic else None,
                                       "stage": stage, "why": f"{src} · line {line} · ask you",
                                       "fleet": fleet_item.id, "time": iso(self.now)})
        self.log(src, line, f"ask you: {question}", "op", subject=identity)

    def raise_approval(self, attn_id: str, question: str, identity: str, is_epic: bool):
        return self.ports.attention.raise_item(
            project=self.space, kind="decision", owner="user", source="canvas",
            source_reference=f"canvas:{self.space}:{attn_id}:{uuid4().hex[:8]}", headline=short(question),
            context_reference=f"fleet://projects/{self.space}/work/{identity}", actor=self.actor,
            work_item=identity, options=("Accept" if is_epic else "Approve", "Send back"))

    def enter_stage(self, identity: str, stage: str, why: str) -> None:
        state = self.state(identity)
        previous = state.get("stage")
        workflow = self.workflow()
        version = state.get("pin") or workflow["version"]
        if previous and previous != stage:
            for attn_id in ("ap-" + identity,):
                self.close_attn(attn_id, "moved on")
        state["stage"] = stage
        state["stage_entered_at"] = iso(self.now)
        if stage == "done":
            self.stop_runs(identity, "Finished", pause=False)
            self.log(f"workflow v{version}", 0, f"{self.title(identity)} → Done ({why})", "info", subject=identity)
            item = self.items[identity]
            if item.condition != "complete":
                self.items[identity] = self.ports.work.set(identity, actor=self.actor, condition="complete",
                                                            next_step=None)
            return
        record = self.get("stage", stage)
        compiled = self.compiled("stage", stage)
        facts = state["facts"]
        for line in compiled.ops("when"):
            if line.op == "submitted":
                facts["submitted"] = False
            elif line.op == "evidence":
                facts.setdefault("evidence", {}).pop(line.args[0], None)
            elif line.op == "approved":
                facts["approved"] = False
        if self.items[identity].condition == "complete":
            self.items[identity] = self.ports.work.set(identity, actor=self.actor, condition="none")
        self.log(f"workflow v{version}", 0, f"{self.title(identity)} → {record['name']} ({why})", "info",
                 subject=identity)
        self.run_section("stage", record, "enter", identity)

    def close_attn(self, attn_id: str, why: str) -> None:
        record = self.get("attn", attn_id)
        if record is None:
            return
        self.drop("attn", attn_id)
        try:
            fleet = self.ports.attention.get(record["fleet"])
            if fleet.state != "resolved":
                self.ports.attention.resolve(record["fleet"], details=f"canvas: {why}", actor=self.actor)
        except LookupError:
            pass

    def advance(self, identity: str) -> bool:
        state = self.state(identity)
        check = self.when_ok(identity)
        if not check["ok"]:
            return False
        flow = self.flow(state)
        if state["stage"] not in flow:
            return False
        following = flow[flow.index(state["stage"]) + 1]
        self.enter_stage(identity, following, f"{check['src']} exit conditions met")
        return True

    def resume(self, identity: str, src: str, line: int) -> None:
        state = self.state(identity)
        was = state.get("paused")
        if was and state.get("paused_by") == "budget" and state.get("budget"):
            spent = sum(run.get("cost") or 0 for run in self.runs_for(identity, active=False))
            state["budget"] = round(spent + state["budget"], 2)
            self.log(src, line, f"budget raised to ${state['budget']:g} to resume {self.title(identity)}", "op",
                     subject=identity)
        state["paused"], state["paused_by"] = False, None
        for run in self.all("run").values():
            if run["item"] != identity:
                continue
            if run["state"] == "paused":
                self.requeue(run)
            elif run.get("cancel") and run["state"] in BUSY_RUN:
                if run.get("cancel_sent"):
                    run["requeue"] = True  # the host is stopping it; queue it again once it has stopped
                else:
                    run.update(cancel=False, pause=False, queue_reason="")
        if was or line:
            self.log(src, line, f"resume runs → {self.title(identity)}", "op", subject=identity)

    def requeue(self, run: dict) -> None:
        """Queue a stopped run again as a new attempt, so its next start is a new job rather than a replay."""
        run.update(state="queued", queue_reason="Resumed; waiting for the scheduler", fleet_run=None, host=None,
                   job=None, ended_at=None, started_at=None, requeue=False, attempt=run.get("attempt", 1) + 1,
                   **FRESH_OUTBOX)

    # ---- regions
    def region_of(self, identity: str) -> dict | None:
        region = self.state(identity).get("region")
        return self.get("region", region) if region else None

    def guard_region(self, region: dict, identity: str | None, *, document: bool = False) -> None:
        if region.get("level") != "enforced":
            return
        compiled = self.compiled("region", region["id"])
        for line in compiled.ops("capacity"):
            if line.op == "documents_only" and not document:
                raise self.refuse("wrong_kind", f"{region['name']} accepts documents and notes, not tasks.",
                                  kind="region", record=region, line=line.n)
            if line.op == "limit" and not document:
                inside = [other for other in self.card_ids() if other != identity
                          and self.state(other).get("region") == region["id"] and self.state(other).get("stage") != "done"]
                limit = int(line.args[0])
                if len(inside) >= limit:
                    raise self.refuse("capacity_full",
                                      f"{region['name']} already holds {len(inside)} unfinished items (limit {limit}).",
                                      kind="region", record=region, line=line.n)

    def enter_region(self, identity: str, region: dict) -> None:
        state = self.state(identity)
        state["region"] = region["id"]
        state["parked_at"] = iso(self.now)
        effect = " (label only: no effect)" if region.get("level") == "label" else ""
        self.log(self.label("region", region), 0, f"{self.title(identity)} entered {region['name']}{effect}", "info",
                 subject=identity)
        self.run_section("region", region, "enter", identity)

    def leave_region(self, identity: str) -> None:
        state = self.state(identity)
        region = self.region_of(identity)
        state["region"] = None
        if region is None:
            return
        self.log(self.label("region", region), 0, f"{self.title(identity)} left {region['name']}", "info",
                 subject=identity)
        self.run_section("region", region, "exit", identity)
        if state.get("paused") and state.get("paused_by") == self.label("region", region):
            pass

    def region_agents_rule(self, region: dict, op: str) -> bool:
        return region.get("level") == "enforced" and self.compiled("region", region["id"]).has("agents", op)

    def placement_region(self) -> dict | None:
        for region in self.all("region").values():
            if self.region_agents_rule(region, "placement"):
                return region
        return None

    # ---- epics
    def epic_compiled(self) -> Compiled:
        record = self.get("epicflow", "main")
        return compile_code(record["code"] if record else defaults.EPIC_WORKFLOW)

    def children(self, epic: str) -> list[str]:
        return [identity for identity in self.card_ids() if self.epic_of(identity) == epic]

    def gaps(self, epic: str) -> list:
        children = self.children(epic)
        covered = set()
        for child in children:
            if self.has_criteria(child):
                covered.update(self.state(child).get("covers", []))
        return [criterion for criterion in self.criteria.get(epic, []) if criterion.id not in covered]

    def epic_step(self, epic: str) -> None:
        state = self.epic_state(epic)
        record = self.get("epicflow", "main") or {"version": 1, "id": "main"}
        compiled = self.epic_compiled()
        src = self.label("epicflow", record)
        stage = state["stage"]
        if stage not in compiled.stages:
            return
        children = self.children(epic)
        if state.get("held") is not None:
            if state["held"] == self.epic_signature(epic):
                return
            state["held"] = None
        for line in compiled.ops("when", stage=stage):
            if line.op == "covered" and (self.gaps(epic) or not self.criteria.get(epic)):
                return
            if line.op == "children_done" and (not children or any(self.state(c).get("stage") != "done" for c in children)):
                return
            if line.op == "approved" and not state.get("approved"):
                return
        order = list(compiled.stages) + ["done"]
        following = order[order.index(stage) + 1]
        state["stage"] = following
        line_no = next((line.n for line in compiled.lines if line.kind == "header" and line.stage == stage), 0)
        self.log(src, line_no, f"{self.title(epic)}: exit conditions of {stage} met → {following.capitalize()}", "op",
                 subject=epic)
        if following == "done":
            if self.items[epic].condition != "complete":
                self.items[epic] = self.ports.work.set(epic, actor=self.actor, condition="complete")
            return
        state["approved"] = False
        for line in compiled.guidance("enter", stage=following):
            self.log(src, line.n, f"guidance passed to the epic owner: “{line.text}”", "guide", subject=epic)
        for line in compiled.ops("enter", stage=following):
            self.apply_line("epicflow", record, line, epic)

    def epic_signature(self, epic: str) -> list:
        """What would make an epic sent back worth moving on again: its children, their stages, its criteria."""
        return sorted([child, self.state(child).get("stage") or "", ",".join(sorted(self.state(child).get("covers", [])))]
                      for child in self.children(epic)) + [sorted(c.id for c in self.criteria.get(epic, []))]

    def decompose(self, epic: str, *, author: str) -> dict | None:
        """Propose a child task for each epic criterion no child covers; adopting it creates them."""
        gaps = self.gaps(epic)
        operations, lines = [], []
        children = self.children(epic)
        for criterion in gaps:
            holder = next((child for child in children if criterion.id in self.state(child).get("covers", [])), None)
            if holder is not None:
                lines.append(f"“{criterion.text}” is mapped to {self.title(holder)}, which has no acceptance "
                             "criteria yet; write them rather than adding a task.")
                continue
            operations.append({"op": "item.create", "args": {
                "title": criterion.text[:120], "epic": epic, "criteria": [criterion.text], "covers": [criterion.id],
                "stage": "first"}})
        if not operations:
            return None
        desc = (f"Create {len(operations)} child task{'s' if len(operations) != 1 else ''} of {self.title(epic)}, "
                "each covering one uncovered criterion")
        return self.propose(author, desc, operations, kind="decomposition", subject=epic, notes=lines)

    # ---- proposals
    def propose(self, author: str, desc: str, operations: list[dict], *, kind: str, subject: str | None = None,
                notes: list[str] = (), conversation: str | None = None) -> dict:
        proposal_id = self.new_id("prop")
        return self.put("proposal", proposal_id, {
            "id": proposal_id, "author": author, "kind": kind, "desc": desc, "operations": operations,
            "state": "open", "time": iso(self.now), "subject": subject, "notes": list(notes),
            "conversation": conversation})

    # ---- dependencies
    def depends(self) -> list[tuple[str, str]]:
        """(first, waits) pairs: `waits` cannot start until `first` is done."""
        return [(relation.to_item, relation.from_item) for relation in self.relations]

    def waits_on(self, identity: str) -> list[str]:
        return [first for first, waits in self.depends() if waits == identity
                and self.state(first).get("stage") != "done" and self.items[first].condition != "complete"]

    def reaches(self, start: str, goal: str) -> bool:
        edges: dict[str, list[str]] = {}
        for first, waits in self.depends():
            edges.setdefault(first, []).append(waits)
        stack, seen = [start], set()
        while stack:
            node = stack.pop()
            if node == goal:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(edges.get(node, []))
        return False

    # ---- status
    def status(self, identity: str) -> str:
        state = self.state(identity)
        if state.get("stage") == "done":
            return "done"
        if state.get("paused"):
            return "paused"
        run = self.active_run(identity)
        if run is not None:
            return {"queued": "queued", "starting": "queued", "running": "working", "struggling": "struggling",
                    "blocked": "blocked"}[run["state"]]
        if self.get("attn", "ap-" + identity) is not None:
            return "waiting-you"
        check = self.when_ok(identity)
        if not check["ok"] and check.get("text") == "spec has acceptance criteria":
            return "waiting-criteria"
        return "idle"
