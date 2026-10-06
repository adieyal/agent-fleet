"""Every operation the canvas, the reader, the CLI and agents can send."""
from __future__ import annotations

import re
from copy import deepcopy

from ..domain import defaults
from ..domain.language import (BANDS, LEVELS, SCOPE_KINDS, SCOPE_LEVELS, VIEW_DEFAULTS, VIEW_TYPES, CodeInvalid,
                               compile_code, compile_schedule, compile_view, header_of, replace_header_version,
                               view_code)
from . import orchestrator
from .kernel import ACTIVE_RUN, ZONE_COLORS, Refused, is_person, iso
from .tick import Ticking

CODE_KINDS = {"stage": "stage", "zone": "region", "region": "region", "schedule": "schedule",
              "epicflow": "epicflow", "epic workflow": "epicflow", "view": "view"}
MIGRATIONS = ("move", "pin")
NAME = re.compile(r"\S.{0,79}\Z")


def text_arg(args: dict, name: str, *, required: bool = True, limit: int = 4000) -> str | None:
    value = args.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Refused("invalid", f"{name} is required")
    if len(value) > limit:
        raise Refused("invalid", f"{name} is longer than {limit} characters")
    return value.strip()


def rect_arg(args: dict) -> dict:
    rect = args.get("rect")
    if not isinstance(rect, dict) or not all(isinstance(rect.get(key), (int, float)) for key in ("x", "y", "w", "h")):
        raise Refused("invalid", "rect needs numeric x, y, w and h")
    if rect["w"] < 80 or rect["h"] < 60:
        raise Refused("invalid", "drag out a larger region to create one")
    return {key: round(float(rect[key]), 1) for key in ("x", "y", "w", "h")}


class Engine(Ticking):
    OPERATIONS = (
        "item.move", "item.set_band", "item.reparent", "item.cover", "item.create", "epic.create",
        "region.enter", "region.exit", "region.propose", "region.create", "region.configure", "region.remove",
        "run.request", "run.start", "run.pause", "run.resume", "run.permit", "run.reassign",
        "dep.add", "dep.remove", "attention.resolve", "message.send", "proposal.resolve",
        "code.compile", "stage.draft", "workflow.insert", "criteria.set", "charter.update", "epic.decompose",
        "view.place", "view.configure", "view.remove", "context.add", "context.remove", "reader.mark_seen",
        "page.update", "spec.update", "note.dismiss", "agent.configure", "decision.check", "tick", "space.init",
    )

    def op_space_init(self, args: dict) -> dict:
        """Give a project a canvas: the starting workflow, scheduler, epic workflow, Inbox, charter and page."""
        if self.get("workflow", "main") is not None:
            return {"created": False}
        name = args.get("name") if isinstance(args.get("name"), str) else self.space
        author = "fleet"
        for identity, title, code in defaults.STAGES:
            self.put("stage", identity, {"id": identity, "name": title, "code": code, "version": 1,
                                         "written_by": author, "adopted_by": self.actor, "added_in": 1,
                                         "created_at": iso(self.now)})
            self.snapshot("stage", identity)
        stages = [identity for identity, _, _ in defaults.STAGES]
        self.put("workflow", "main", {"id": "main", "version": 1, "stages": stages,
                                      "history": [{"version": 1, "stages": list(stages)}]})
        self.put("schedule", "main", {"id": "main", "code": defaults.SCHEDULE, "version": 1, "written_by": author,
                                      "adopted_by": self.actor})
        self.put("epicflow", "main", {"id": "main", "code": defaults.EPIC_WORKFLOW, "version": 1,
                                      "written_by": author, "adopted_by": self.actor})
        self.put("region", "inbox", {"id": "inbox", "name": "Inbox", "code": defaults.INBOX, "level": "enforced",
                                     "version": 1, "rect": {"x": 1500, "y": 120, "w": 340, "h": 300}, "color": None,
                                     "written_by": author, "adopted_by": self.actor, "created_at": iso(self.now)})
        self.put("charter", "main", {"id": "main", "version": 1, "north_star": args.get("north_star") or "",
                                     "clauses": [], "scope": dict(defaults.SCOPE), "written_by": self.actor})
        self.put("page", "main", {"id": "main", "markdown": defaults.PAGE.format(name=name), "version": 1})
        self.put("spec", "main", {"id": "main", "text": args.get("spec") or "", "version": 1})
        self.put("settings", "main", {"id": "main", "agents": {}, "allow": []})
        for kind in ("schedule", "epicflow", "region"):
            for identity in self.all(kind):
                self.snapshot(kind, identity)
        self.log("workflow v1", 0, f"adopted by {self.who()}: plan → implement → approve", "info")
        return {"created": True}
    last_seq = 0

    def apply(self, op: str, args: dict) -> dict:
        if op not in self.OPERATIONS:
            raise Refused("invalid", f"unknown operation '{op}'")
        if not isinstance(args, dict):
            raise Refused("invalid", "operation arguments must be an object")
        result = getattr(self, "op_" + op.replace(".", "_"))(args)
        return result or {}

    # ---- items
    def op_item_move(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        target = text_arg(args, "stage")
        flow = self.flow(state)
        workflow = self.workflow()
        source = {"object": "workflow", "version": state.get("pin") or workflow["version"], "line": 0}
        title = self.title(identity)
        if target not in flow:
            if target in self.all("stage"):
                raise Refused("stage_skipped", f"{title} finishes under workflow v{state['pin']}, which has no "
                              f"{self.stage_name(target)} stage.", source=source)
            raise Refused("not_found", f"no stage '{target}' in workflow v{workflow['version']}")
        current = state.get("stage")
        if not current:
            if target != flow[0]:
                raise Refused("stage_skipped", f"New items enter at {self.stage_name(flow[0])}.", source=source)
            if state.get("region"):
                self.leave_region(identity)
            self.enter_stage(identity, target, f"moved by {self.who()}")
            return {"stage": target}
        if target == current:
            if state.get("region"):
                self.leave_region(identity)
            return {"stage": target}
        here, there = flow.index(current), flow.index(target)
        if there < here:
            if state.get("region"):
                self.leave_region(identity)
            self.enter_stage(identity, target, f"sent back by {self.who()}")
            return {"stage": target}
        if there > here + 1:
            skipped = ", ".join(self.stage_name(stage) for stage in flow[here + 1:there])
            raise Refused("stage_skipped", f"Workflow v{source['version']} does not let {title} skip {skipped}.",
                          source=source)
        check = self.when_ok(identity)
        if not check["ok"]:
            record = check["record"]
            raise Refused("exit_condition_unmet", f"{title} cannot leave {record['name']}: “{check['text']}” is not "
                          "true yet.", source=self.source("stage", record, check["line"]), condition=check["text"])
        if state.get("region"):
            self.leave_region(identity)
        self.enter_stage(identity, target, f"moved by {self.who()}; {check['src']} exit conditions met")
        return {"stage": target}

    def op_item_set_band(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        band = text_arg(args, "band").lower()
        if band not in BANDS:
            raise Refused("invalid", "band is now, next or later")
        if band == (state.get("band") or "later"):
            return {"band": band}
        policy = self.schedule().options
        limit = policy["bands"].get(band)
        if limit is not None and state.get("stage") != "done":
            held = [other for other in self.card_ids() if other != identity
                    and (self.state(other).get("band") or "later") == band and self.state(other).get("stage") != "done"]
            if len(held) >= limit:
                record = self.get("schedule", "main") or {"version": 1}
                raise Refused("capacity_full", f"{band.capitalize()} already holds {len(held)} unfinished items.",
                              source={"object": "schedule", "version": record["version"],
                                      "line": policy["lines"].get("band:" + band, 0)})
        state["band"] = band
        self.log(self.who_source(), 0, f"{self.title(identity)} → band {band.capitalize()}", "info", subject=identity)
        return {"band": band}

    def op_item_reparent(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        epic = args.get("epic")
        if epic is not None:
            epic = self.resolve_item(epic)
            if self.items[epic].kind != "epic":
                raise Refused("invalid", f"{self.title(epic)} is not an epic")
        old = self.epic_of(identity)
        if old == epic:
            return {"epic": epic}
        try:
            self.items[identity] = self.ports.work.move(identity, parent=epic, actor=self.actor)
        except ValueError as error:
            raise Refused("cycle" if "cycle" in str(error) else "invalid", str(error)) from error
        state["covers"] = []
        self.log(self.who_source(), 0, f"{self.title(identity)} moved from {self.title(old) if old else 'no epic'} to "
                 f"{self.title(epic) if epic else 'no epic'}", "info", subject=identity)
        warning = None
        if epic:
            warning = (f"Moved to {self.title(epic)}. It covers none of its criteria yet, so it won't count toward the "
                       "epic until you map it.")
            self.toasts.append(warning)
        return {"epic": epic, "warning": warning}

    def op_item_cover(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        epic = self.epic_of(identity)
        criteria = args.get("criteria")
        if not isinstance(criteria, list) or not all(isinstance(value, str) for value in criteria):
            raise Refused("invalid", "criteria is a list of the epic's criterion ids")
        known = {criterion.id for criterion in self.criteria.get(epic, [])} if epic else set()
        unknown = [value for value in criteria if value not in known]
        if unknown:
            raise Refused("invalid", "those criteria are not the epic's: " + ", ".join(unknown))
        state["covers"] = list(dict.fromkeys(criteria))
        self.log(self.who_source(), 0, f"{self.title(identity)} now covers {len(criteria)} of "
                 f"{self.title(epic) if epic else 'its epic'}'s criteria", "info", subject=identity)
        return {"covers": state["covers"]}

    def op_item_create(self, args: dict) -> dict:
        title = text_arg(args, "title", limit=200)
        goal = text_arg(args, "goal", required=False) or title
        epic = args.get("epic")
        if epic is not None:
            epic = self.resolve_item(epic)
        item = self.ports.work.add(project=self.space, title=title, goal=goal, actor=self.actor, kind="task", parent=epic)
        self.items[item.id] = item
        self.criteria[item.id] = []
        state = self.state(item.id)
        for text in args.get("criteria") or []:
            self.criteria[item.id].append(self.ports.work.add_criterion(item.id, text=text, verification="judged",
                                                                        actor=self.actor))
        if args.get("covers"):
            state["covers"] = [value for value in args["covers"] if isinstance(value, str)]
        self.log(self.who_source(), 0, f"created {title}" + (f" in {self.title(epic)}" if epic else ""), "info",
                 subject=item.id)
        if args.get("stage") == "first":
            self.enter_stage(item.id, self.flow(state)[0], "created in the workflow")
        elif args.get("region"):
            region = self.resolve_region(args["region"])
            self.enter_region(item.id, region)
        else:
            placement = self.placement_region()
            if placement is not None:
                self.enter_region(item.id, placement)
        return {"item": item.id}

    def op_epic_create(self, args: dict) -> dict:
        title = text_arg(args, "title", limit=200)
        goal = text_arg(args, "goal", required=False) or title
        item = self.ports.work.add(project=self.space, title=title, goal=goal, actor=self.actor, kind="epic")
        self.items[item.id] = item
        self.criteria[item.id] = []
        for text in args.get("criteria") or []:
            self.criteria[item.id].append(self.ports.work.add_criterion(item.id, text=text, verification="accepted",
                                                                        actor=self.actor))
        self.epic_state(item.id)
        record = self.get("epicflow", "main") or {"version": 1}
        self.log(f"epic workflow v{record['version']}", 0, f"epic “{title}” opened in Shape by {self.who()}", "info",
                 subject=item.id)
        return {"epic": item.id}

    # ---- regions
    def resolve_region(self, reference) -> dict:
        if not isinstance(reference, str) or not reference:
            raise Refused("invalid", "a region is required")
        if reference.startswith("@"):
            name = reference[1:].lower()
            found = next((region for region in self.all("region").values() if region["name"].lower() == name), None)
            if found is None:
                raise Refused("not_found", f"no region named {reference[1:]}")
            return found
        return self.need("region", reference, "region")

    def agent_rules(self, region: dict, identity: str, entering: bool) -> dict | None:
        """Agents' limits in a region; a proposal is returned when the region lets agents only propose."""
        if is_person(self.actor):
            return None
        compiled = self.compiled("region", region["id"]) if region.get("level") == "enforced" else None
        if compiled is None:
            return None
        if not entering and compiled.has("agents", "no_exit"):
            line = next(line for line in compiled.ops("agents") if line.op == "no_exit")
            raise Refused("not_permitted", f"Agents may not move items out of {region['name']}.",
                          source=self.source("region", region, line.n))
        if entering and compiled.has("agents", "may_propose"):
            return self.propose(self.actor, f"Move {self.title(identity)} into {region['name']}",
                                [{"op": "region.enter", "args": {"region": region["id"], "item": identity}}],
                                kind="entry", subject=identity)
        return None

    def op_region_enter(self, args: dict) -> dict:
        region = self.resolve_region(args.get("region"))
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        if state.get("region") == region["id"]:
            return {"region": region["id"]}
        proposal = self.agent_rules(region, identity, True)
        if proposal is not None:
            return {"proposal": proposal["id"]}
        if state.get("region"):
            self.agent_rules(self.need("region", state["region"]), identity, False)
        self.guard_region(region, identity)
        if state.get("region"):
            self.leave_region(identity)
        self.enter_region(identity, region)
        return {"region": region["id"]}

    def op_region_exit(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        if not state.get("region"):
            return {"region": None}
        region = self.need("region", state["region"], "region")
        if args.get("region") not in (None, region["id"]):
            raise Refused("invalid", f"{self.title(identity)} is in {region['name']}, not that region")
        self.agent_rules(region, identity, False)
        self.leave_region(identity)
        message = None
        if state.get("stage") and state["stage"] != "done":
            message = f"{self.title(identity)} returned to its column: open canvas carries no meaning"
            self.log("canvas", 0, message, "info", subject=identity)
        return {"region": None}

    def op_region_propose(self, args: dict) -> dict:
        name = text_arg(args, "name", limit=80)
        rect = rect_arg(args)
        if any(region["name"].lower() == name.lower() for region in self.all("region").values()):
            raise Refused("invalid", f"there is already a region called {name}")
        code = defaults.zone_code(name)
        proposal = self.propose("orchestrator", f"Create region “{name}” with the code below",
                                [{"op": "region.create", "args": {"name": name, "rect": rect, "code": code}}],
                                kind="region", subject=None)
        proposal["code"] = code
        return {"proposal": proposal["id"], "code": code,
                "compiled": compile_code(code).as_dict()}

    def op_region_create(self, args: dict) -> dict:
        name = text_arg(args, "name", limit=80)
        rect = rect_arg(args)
        level = args.get("level") or "enforced"
        if level not in LEVELS:
            raise Refused("invalid", "level is enforced, guidance or label")
        if any(region["name"].lower() == name.lower() for region in self.all("region").values()):
            raise Refused("invalid", f"there is already a region called {name}")
        if args.get("color") is not None and args.get("color") not in ZONE_COLORS:
            raise Refused("invalid", "choose one of the region colours")
        code = args.get("code") or defaults.zone_code(name)
        if header_of(code) != f'zone "{name}"':
            raise Refused("invalid", f'the region\'s code must start with zone "{name}"')
        try:
            compile_code(code, level=level)
        except CodeInvalid as error:
            raise Refused("invalid", str(error)) from error
        identity = self.new_id("zone")
        author = args.get("written_by") or self.actor
        self.put("region", identity, {"id": identity, "name": name, "code": code, "level": level, "version": 1,
                                      "rect": rect, "color": args.get("color"), "written_by": author,
                                      "adopted_by": self.actor, "created_at": iso(self.now)})
        self.snapshot("region", identity)
        self.log(f"zone {name} v1", 0, f"created by {self.who()} as {level}" +
                 ("" if author == self.actor else f"; code drafted by {author}"), "info")
        return {"region": identity}

    def op_region_configure(self, args: dict) -> dict:
        region = self.resolve_region(args.get("region"))
        if "level" in args:
            level = args["level"]
            if level not in LEVELS:
                raise Refused("invalid", "level is enforced, guidance or label")
            if level != region["level"]:
                region["level"] = level
                region["version"] += 1
                self.snapshot("region", region["id"])
                self.log(self.label("region", region), 0, f"level set to {level} by {self.who()}", "info")
        presentation = False
        if "color" in args:
            color = args["color"]
            if color is not None and color not in ZONE_COLORS:
                raise Refused("invalid", "choose one of the region colours")
            region["color"] = color
            presentation = True
        if "rect" in args:
            region["rect"] = rect_arg(args)
            presentation = True
        if presentation and "level" not in args:
            self.presentation.add(("region", region["id"]))
        return {"region": region["id"], "version": region["version"]}

    def op_region_remove(self, args: dict) -> dict:
        region = self.resolve_region(args.get("region"))
        for identity in self.card_ids():
            if self.state(identity).get("region") == region["id"]:
                self.leave_region(identity)
        for key, entry in list(self.all("context").items()):
            if entry.get("region") == region["id"]:
                self.drop("context", key)
        self.drop("region", region["id"])
        self.log(self.label("region", region), 0, f"removed by {self.who()}", "info")
        return {"region": None}

    # ---- runs
    def op_run_request(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        self.require_card(identity)
        role = text_arg(args, "role")
        run = self.request_run(identity, role, self.who_source(), 0)
        return {"run": run["id"]}

    def op_run_start(self, args: dict) -> dict:
        run = self.need("run", text_arg(args, "run"), "run")
        agent = text_arg(args, "agent")
        if run["state"] != "queued":
            raise Refused("invalid", "only a queued run can be started")
        policy = self.schedule().options
        record = self.get("schedule", "main") or {"version": 1}
        capacity = policy["capacity"]
        if agent not in capacity:
            raise Refused("not_permitted", f"schedule v{record['version']} gives {agent} no capacity",
                          source={"object": "schedule", "version": record["version"], "line": 0})
        busy = sum(1 for other in self.all("run").values() if other.get("agent") == agent and other["state"] in
                   ("starting", "running", "struggling", "blocked"))
        if busy >= capacity[agent]:
            raise Refused("capacity_full", f"{agent} is running {busy} of {capacity[agent]} runs",
                          source={"object": "schedule", "version": record["version"],
                                  "line": policy["lines"].get("capacity:" + agent, 0)})
        if policy["dependencies"] and self.waits_on(run["item"]):
            raise Refused("not_permitted", "Waiting on " + ", ".join(self.title(other) for other in self.waits_on(run["item"])),
                          source={"object": "schedule", "version": record["version"],
                                  "line": policy["lines"].get("dependencies", 0)})
        builder = self.state(run["item"]).get("builder")
        if run["role"] == "tester" and agent == builder and (policy["independent_testers"] or run.get("independent")):
            raise Refused("not_permitted", f"the tester must be independent of builder {builder}",
                          source={"object": "schedule", "version": record["version"],
                                  "line": policy["lines"].get("independence", 0)})
        if agent not in self.agents():
            raise Refused("invalid", f"no host is set up for {agent}")
        self.assign(run, agent, busy + 1, capacity[agent], self.who_source(), 0)
        return {"run": run["id"], "agent": agent}

    def op_run_pause(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        if not state.get("paused"):
            state["paused"], state["paused_by"] = True, self.who_source()
            self.stop_runs(identity, f"Paused by {self.who()}", pause=True)
            self.log(self.who_source(), 0, f"paused {self.title(identity)}", "info", subject=identity)
        return {"paused": True}

    def op_run_resume(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        state = self.require_card(identity)
        paused_by = state.get("paused_by") or ""
        if paused_by.startswith("zone "):
            region = self.region_of(identity)
            raise Refused("not_permitted", "It is paused by a region; drag it out to resume.",
                          source=self.source("region", region, 0) if region else None)
        self.resume(identity, self.who_source(), 0)
        if not state.get("paused"):
            self.log(self.who_source(), 0, f"resumed {self.title(identity)}", "info", subject=identity)
        return {"paused": False}

    def op_run_permit(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        self.require_card(identity)
        scope = text_arg(args, "scope")
        if scope not in ("run", "space", "everywhere"):
            raise Refused("invalid", "scope is run, space or everywhere")
        run = self.active_run(identity)
        if run is None or run["state"] not in ("struggling", "blocked"):
            raise Refused("invalid", f"{self.title(identity)} has no run waiting for a permission")
        run.update(permit=scope, permitted=False, permit_error=None, permit_item=None)
        attn = self.get("attn", "perm-" + identity)
        if attn is not None:
            run["permit_item"] = attn.get("fleet")
            self.drop("attn", "perm-" + identity)
        what = {"run": "for this run only", "space": "for runs in this space",
                "everywhere": "everywhere (a rule version bump)"}[scope]
        self.log(self.who_source(), 0, f"allowed the refused commands {what}; {self.title(identity)} continues",
                 "op" if scope == "everywhere" else "info", subject=identity)
        return {"run": run["id"], "scope": scope}

    def op_run_reassign(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        self.require_card(identity)
        agent = text_arg(args, "agent")
        if agent not in self.schedule().options["capacity"]:
            raise Refused("not_permitted", f"the schedule gives {agent} no capacity")
        run = self.active_run(identity)
        if run is None:
            raise Refused("invalid", f"{self.title(identity)} has no run to reassign")
        was = run.get("agent")
        role = run["role"]
        if run["state"] == "queued":
            run["force_agent"] = agent
        elif run["state"] == "starting" and not run.get("fleet_run"):
            run.update(state="stopped", outcome="reassigned", dispatch=False, ended_at=iso(self.now))
            new = self.request_run(identity, role, self.who_source(), 0)
            new["force_agent"] = agent
        else:
            run["cancel"], run["pause"] = True, False
            run["state"] = "stopped"
            run["outcome"] = "reassigned"
            new = self.request_run(identity, role, self.who_source(), 0)
            new["force_agent"] = agent
        self.drop("attn", "perm-" + identity)
        self.log(self.who_source(), 0, f"{self.title(identity)}: stopped {was or 'the queued run'}, reassigned to {agent}",
                 "info", subject=identity)
        return {"agent": agent}

    # ---- dependencies
    def op_dep_add(self, args: dict) -> dict:
        first = self.resolve_item(text_arg(args, "from"))
        waits = self.resolve_item(text_arg(args, "to"))
        self.require_card(first)
        self.require_card(waits)
        if first == waits:
            raise Refused("cycle", "a task cannot wait for itself")
        if (first, waits) in self.depends():
            return {"from": first, "to": waits}
        if self.reaches(waits, first):
            raise Refused("cycle", "Refused: that would create a cycle.")
        relation = self.ports.work.relate(waits, first, actor=self.actor, type="depends-on")
        self.relations.append(relation)
        self.log(self.who_source(), 0, f"{self.title(waits)} now depends on {self.title(first)}", "info", subject=waits)
        return {"from": first, "to": waits}

    def op_dep_remove(self, args: dict) -> dict:
        first = self.resolve_item(text_arg(args, "from"))
        waits = self.resolve_item(text_arg(args, "to"))
        if (first, waits) not in self.depends():
            return {}
        self.ports.work.unrelate(waits, first, actor=self.actor, type="depends-on")
        self.relations = [relation for relation in self.relations
                          if not (relation.from_item == waits and relation.to_item == first)]
        self.log(self.who_source(), 0, f"removed dependency: {self.title(waits)} no longer waits for "
                 f"{self.title(first)}", "info", subject=waits)
        return {}

    # ---- decisions
    def op_attention_resolve(self, args: dict) -> dict:
        reference = text_arg(args, "id")
        choice = args.get("choice", "approve")
        record = self.get("attn", reference) or next(
            (entry for entry in self.all("attn").values() if entry.get("fleet") == reference), None)
        if record is None:
            answer = text_arg(args, "answer", required=False) or choice
            decision = self.ports.decisions.answer(reference, answer, actor=self.actor)
            self.log(self.who_source(), 0, f"answered “{decision.question}”: {decision.answer}", "info")
            return {"decision": decision.id}
        if choice not in ("approve", "back"):
            raise Refused("invalid", "choice is approve or back")
        if record["kind"] == "Unblock":
            if choice == "approve":
                return self.op_run_permit({"item": record["item"], "scope": "run"})
            return {"open": record["item"]}
        self.drop("attn", record["id"])
        answer = ("Accept" if record["kind"] == "Accept" else "Approve") if choice == "approve" else "Send back"
        decision_id = None
        try:
            fleet = self.ports.attention.get(record["fleet"])
            if fleet.state != "resolved":
                decision_id = self.ports.decisions.answer(record["fleet"], answer, actor=self.actor).id
        except LookupError:
            pass
        self.settle(record, choice, actor=self.actor)
        return {"decision": decision_id, "choice": choice}

    def op_decision_check(self, args: dict) -> dict:
        kind = text_arg(args, "kind")
        if kind not in SCOPE_KINDS:
            raise Refused("invalid", "kind is one of " + ", ".join(SCOPE_KINDS))
        charter = self.get("charter", "main")
        scope = (charter or {}).get("scope", {})
        level = scope.get(kind, "ask")
        rule = args.get("rule")
        for clause in (charter or {}).get("clauses", []):
            if clause.get("kind") == "enforced" and defaults.RULES.get(clause.get("rule")) == kind and \
                    (rule is None or rule == clause.get("rule")) and level != "ask":
                return {"level": "ask", "scope": level, "outranked_by": clause["rule"],
                        "code": "rule_outranks_scope", "charter_version": charter["version"]}
        return {"level": level, "scope": level, "charter_version": (charter or {}).get("version")}

    # ---- messages and proposals
    def op_message_send(self, args: dict) -> dict:
        text = text_arg(args, "text")
        target = args.get("target")
        if target is not None:
            if not isinstance(target, dict) or target.get("kind") not in ("task", "session", "epic", "stage", "zone",
                                                                          "region", "sched", "view"):
                raise Refused("invalid", "target names a task, epic, stage, region, view or the scheduler")
            if target["kind"] == "session":
                target = {"kind": "task", "id": target["id"]}
            if target["kind"] in ("task", "epic"):
                target = {"kind": target["kind"], "id": self.resolve_item(target.get("id"))}
        key = "orch" if target is None else f"{target['kind']}:{target.get('id')}"
        mine = self.new_id("msg")
        order = len(self.all("message"))
        self.put("message", mine, {"id": mine, "key": key, "who": self.actor, "text": text, "time": iso(self.now),
                                   "proposals": [], "order": order})
        label = "the orchestrator" if target is None else self.target_label(target)
        self.log(self.who_source(), 0, f"to {label}: {text}", "info",
                 subject=target.get("id") if target and target["kind"] in ("task", "epic") else None)
        answer = orchestrator.reply(self, text, target)
        if answer["guidance"] and target is not None:
            guide = self.new_id("guide")
            self.put("guidance", guide, {"id": guide, "target": key, "text": text, "author": self.actor,
                                         "source": f"message {mine}", "time": iso(self.now)})
        proposals = [self.propose("orchestrator", desc, operations, kind="message", conversation=key)["id"]
                     for desc, operations in answer["proposals"]]
        theirs = self.new_id("msg")
        self.put("message", theirs, {"id": theirs, "key": key, "who": answer["who"], "text": answer["text"],
                                     "time": iso(self.now), "proposals": proposals, "order": order + 1})
        self.log(answer["who"], 0, "replied: " + answer["text"].split("\n")[0][:90], "info")
        return {"reply": theirs, "proposals": proposals}

    def target_label(self, target: dict) -> str:
        kind, identity = target["kind"], target.get("id")
        if kind == "task":
            return f"orchestrator · {self.title(identity)}"
        if kind == "epic":
            return f"owner of {self.title(identity)}"
        if kind == "stage":
            return f"orchestrator · stage {self.stage_name(identity)}"
        if kind in ("zone", "region"):
            region = self.get("region", identity)
            return f"orchestrator · region {region['name'] if region else identity}"
        if kind == "sched":
            return "orchestrator · scheduler"
        if kind == "view":
            view = self.get("view", identity)
            return f"orchestrator · view {view['title'] if view else identity}"
        return "the orchestrator"

    def op_proposal_resolve(self, args: dict) -> dict:
        proposal = self.need("proposal", text_arg(args, "id"), "proposal")
        if proposal["state"] != "open":
            raise Refused("invalid", f"that proposal is already {proposal['state']}")
        adopt = args.get("adopt")
        if not isinstance(adopt, bool):
            raise Refused("invalid", "adopt is true or false")
        if not adopt:
            proposal["state"] = "discarded"
            self.log(self.who_source(), 0, f"discarded proposal: {proposal['desc']}", "info")
            return {"state": "discarded"}
        results = []
        for operation in deepcopy(proposal["operations"]):
            arguments = dict(operation["args"])
            if operation["op"] == "region.create":
                arguments["level"] = args.get("level") or arguments.get("level") or "enforced"
                arguments.setdefault("written_by", proposal["author"])
            results.append(self.apply(operation["op"], arguments))
        proposal["state"] = "adopted"
        proposal["adopted_by"] = self.actor
        self.log(self.who_source(), 0, f"adopted proposal by {proposal['author']}: {proposal['desc']}", "info")
        return {"state": "adopted", "results": results}

    # ---- code
    def op_code_compile(self, args: dict) -> dict:
        target = args.get("object")
        if not isinstance(target, dict) or target.get("kind") not in CODE_KINDS:
            raise Refused("invalid", "object names a stage, zone, schedule, epic workflow or view")
        kind = CODE_KINDS[target["kind"]]
        identity = target.get("id") or "main"
        if kind == "region":
            identity = self.resolve_region(identity)["id"]
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise Refused("invalid", "text is the snippet to compile")
        if len(text) > 20000:
            raise Refused("invalid", "code is longer than 20,000 characters")
        if kind in ("schedule", "epicflow") and self.get(kind, "main") is None:
            self.put(kind, "main", {"id": "main", "code": defaults.SCHEDULE if kind == "schedule" else defaults.EPIC_WORKFLOW,
                                    "version": 1, "written_by": "fleet", "adopted_by": "fleet"})
        record = self.code_record(kind, identity)
        base = args.get("base")
        if base is not None and base != record["version"]:
            raise Refused("version_conflict", f"{self.label(kind, record)} is newer than the version you edited "
                          f"(v{base}).", source=self.source(kind, record), current=deepcopy(record))
        head = header_of(text)
        code = text.rstrip()
        try:
            if kind == "stage":
                if head != f"stage {identity}":
                    raise Refused("invalid", f"The first line must stay: stage {identity}.")
                compiled = compile_code(code)
            elif kind == "region":
                if head != f'zone "{record["name"]}"':
                    raise Refused("invalid", f'The first line must stay: zone "{record["name"]}".')
                compiled = compile_code(code, level=record["level"])
            elif kind == "schedule":
                if not re.fullmatch(r"schedule v\d+", head):
                    raise Refused("invalid", f"The first line must stay: schedule v{record['version']}.")
                code = replace_header_version(code, record["version"] + 1)
                compiled = compile_schedule(code)
            elif kind == "epicflow":
                if not re.fullmatch(r"epic workflow v\d+", head):
                    raise Refused("invalid", f"The first line must stay: epic workflow v{record['version']}.")
                code = replace_header_version(code, record["version"] + 1)
                compiled = compile_code(code)
                if not compiled.stages:
                    raise Refused("invalid", "an epic workflow needs at least one stage line")
                missing = [state["stage"] for state in self.all("epic").values()
                           if state["stage"] not in compiled.stages + ("done",)]
                if missing:
                    raise Refused("invalid", "epics are in stages this code removes: " + ", ".join(sorted(set(missing))))
            else:
                match = re.fullmatch(r'view (\w+) "(.*)"', head)
                if not match or match[1] != record["type"]:
                    raise Refused("invalid", f'The first line must stay: view {record["type"]} "Title".')
                compiled = compile_view(code)
        except CodeInvalid as error:
            raise Refused("invalid", str(error)) from error
        record["code"] = code
        record["version"] += 1
        record["written_by"] = args.get("written_by") or self.actor
        record["adopted_by"] = self.actor
        if kind == "view":
            record["title"] = compiled.title or record["title"]
            record["options"] = compiled.options
        self.snapshot(kind, identity)
        ops, guided = compiled.counts()
        detail = f"{ops} operations, {guided} guidance lines"
        if kind == "schedule":
            policy = compiled.options
            detail = ", ".join(f"{agent} {count}" for agent, count in policy["capacity"].items()) + \
                ("" if policy["dependencies"] else "; dependencies no longer respected")
        self.log(self.label(kind, record), 0, f"compiled by {self.who()}: {detail}", "info")
        return {"version": record["version"], "compiled": compiled.as_dict()}

    def op_stage_draft(self, args: dict) -> dict:
        name = args.get("name")
        if self.all("block"):
            raise Refused("invalid", "A new stage is already waiting on the canvas.")
        if name is None:
            if "test" in self.all("stage"):
                raise Refused("invalid", "This workflow already has a Test stage.")
            identity, name, code = defaults.TEST_STAGE
        else:
            name = text_arg(args, "name", limit=40)
            identity = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "stage"
            if not re.match(r"[a-z]", identity):
                identity = "s-" + identity
            if identity in self.all("stage"):
                raise Refused("invalid", f"there is already a stage called {name}")
            code = f"stage {identity}\n  meaning:\n    {name}: describe what has to be true to leave it"
        rect = args.get("at") if isinstance(args.get("at"), dict) else {"x": 1500, "y": 470}
        self.put("block", identity, {"id": identity, "name": name, "code": code, "by": "orchestrator",
                                     "x": rect.get("x", 1500), "y": rect.get("y", 470)})
        self.log("canvas", 0, f"the orchestrator drafted code for a new {name} stage; drag it into the workflow", "info")
        return {"block": identity}

    def op_workflow_insert(self, args: dict) -> dict:
        identity = text_arg(args, "block")
        block = self.need("block", identity, "drafted stage")
        workflow = self.workflow()
        index = args.get("index")
        if not isinstance(index, int) or not 0 <= index <= len(workflow["stages"]):
            raise Refused("invalid", f"index is a position from 0 to {len(workflow['stages'])}")
        if identity in self.all("stage"):
            raise Refused("invalid", f"there is already a stage called {identity}")
        code = args.get("code") or block["code"]
        if header_of(code) != f"stage {identity}":
            raise Refused("invalid", f"The first line must stay: stage {identity}.")
        try:
            compile_code(code)
        except CodeInvalid as error:
            raise Refused("invalid", str(error)) from error
        after = workflow["stages"][index:]
        affected = [card for card in self.card_ids() if self.state(card).get("stage") in after
                    and not self.state(card).get("pin")]
        migration = args.get("migration")
        if affected and migration not in MIGRATIONS:
            raise Refused("invalid", "choose a migration for the work in flight: move or pin")
        old_version = workflow["version"]
        self.make_room(len(workflow["stages"]))
        history = workflow.setdefault("history", [])
        if not any(entry["version"] == old_version for entry in history):
            history.append({"version": old_version, "stages": list(workflow["stages"])})
        workflow["stages"] = workflow["stages"][:index] + [identity] + workflow["stages"][index:]
        workflow["version"] = old_version + 1
        history.append({"version": workflow["version"], "stages": list(workflow["stages"])})
        self.put("stage", identity, {"id": identity, "name": block["name"], "code": code.rstrip(), "version": 1,
                                     "written_by": block.get("by", self.actor), "adopted_by": self.actor,
                                     "added_in": workflow["version"], "created_at": iso(self.now)})
        self.snapshot("stage", identity)
        self.snapshot("workflow", "main")
        self.drop("block", identity)
        flow = " → ".join(self.stage_name(stage).lower() for stage in workflow["stages"])
        self.log(f"workflow v{workflow['version']}", 0, f"inserted {block['name']} (code by {block.get('by')}, adopted "
                 f"by {self.who()}): {flow}", "info")
        for card in affected:
            if migration == "move":
                self.close_attn("ap-" + card, f"migrated to workflow v{workflow['version']}")
                self.enter_stage(card, identity, f"migrated to workflow v{workflow['version']} by {self.who()}")
            else:
                self.state(card)["pin"] = old_version
                self.log(f"workflow v{workflow['version']}", 0, f"{self.title(card)} pinned to workflow v{old_version} "
                         f"by {self.who()}", "info", subject=card)
        return {"version": workflow["version"], "affected": affected}

    def make_room(self, stages: int) -> None:
        """The board grows by one column: shift what sits to its right so nothing ends up under it."""
        right = 60 + 126 + (stages + 1) * 274 - 24
        for region in self.all("region").values():
            if region["rect"]["x"] + region["rect"]["w"] > right - 274 and region["rect"]["x"] >= 60 + 126:
                region["rect"] = dict(region["rect"], x=region["rect"]["x"] + 274)
                self.presentation.add(("region", region["id"]))
        for kind in ("view", "block"):
            for record in self.all(kind).values():
                if record.get("x", 0) >= right - 274 and record.get("y", 0) < 1200:
                    record["x"] = record["x"] + 274
                    if kind == "view":
                        self.presentation.add(("view", record["id"]))

    def op_criteria_set(self, args: dict) -> dict:
        identity = self.resolve_item(text_arg(args, "item"))
        values = args.get("list")
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise Refused("invalid", "list is the acceptance criteria, one string each")
        wanted = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        existing = self.criteria.get(identity, [])
        keep = [criterion for criterion in existing if criterion.text in wanted]
        removed = [criterion for criterion in existing if criterion.text not in wanted]
        known = {criterion.text for criterion in keep}
        is_epic = self.items[identity].kind == "epic"
        for criterion in removed:
            self.ports.work.remove_criterion(criterion.id, actor=self.actor)
        added = [self.ports.work.add_criterion(identity, text=text, verification="accepted" if is_epic else "judged",
                                               actor=self.actor) for text in wanted if text not in known]
        if not removed and not added:
            return {"criteria": [criterion.id for criterion in keep]}
        self.criteria[identity] = keep + added
        if is_epic:
            gone = {criterion.id for criterion in removed}
            for child in self.children(identity):
                state = self.state(child)
                state["covers"] = [value for value in state.get("covers", []) if value not in gone]
        else:
            self.state(identity)["facts"]["evidence"] = {}
        spec = self.get("spec", "main") or self.put("spec", "main", {"id": "main", "text": "", "version": 1})
        spec["version"] += 1
        self.log(f"spec v{spec['version']}", 0, f"{self.who()} wrote acceptance criteria for {self.title(identity)}"
                 + (" (changed criteria lose their evidence)" if removed and not is_epic else ""), "info",
                 subject=identity)
        return {"criteria": [criterion.id for criterion in self.criteria[identity]]}

    def op_charter_update(self, args: dict) -> dict:
        charter = self.get("charter", "main")
        if charter is None:
            charter = self.put("charter", "main", {"id": "main", "version": 1, "north_star": "", "clauses": [],
                                                   "scope": dict(defaults.SCOPE)})
        base = args.get("base")
        if base is not None and base != charter["version"]:
            raise Refused("version_conflict", f"the charter is at v{charter['version']}, newer than v{base}",
                          source={"object": "charter", "version": charter["version"], "line": 0},
                          current=deepcopy(charter))
        patch = args.get("patch")
        if not isinstance(patch, dict) or not patch:
            raise Refused("invalid", "patch is required")
        said = []
        if "north_star" in patch:
            text = text_arg(patch, "north_star", limit=600)
            if text != charter["north_star"]:
                charter["north_star"] = text
                said.append("north star revised")
        if "add_clause" in patch:
            clause = patch["add_clause"]
            text = text_arg(clause if isinstance(clause, dict) else {"text": clause}, "text", limit=400)
            kind = clause.get("kind", "guidance") if isinstance(clause, dict) else "guidance"
            rule = clause.get("rule") if isinstance(clause, dict) else None
            if kind not in ("enforced", "guidance"):
                raise Refused("invalid", "a clause is enforced or guidance")
            if kind == "enforced" and rule not in defaults.RULES:
                raise Refused("invalid", "an enforced clause names a kernel rule: " + ", ".join(defaults.RULES))
            charter["clauses"].append({"text": text, "kind": kind, "rule": rule if kind == "enforced" else None})
            said.append(f"constitution: added “{text}”")
        if "remove_clause" in patch:
            index = patch["remove_clause"]
            if not isinstance(index, int) or not 0 <= index < len(charter["clauses"]):
                raise Refused("invalid", "remove_clause is the index of a clause")
            removed = charter["clauses"].pop(index)
            said.append(f"constitution: removed “{removed['text']}”")
        if "scope" in patch:
            scope = patch["scope"]
            if not isinstance(scope, dict) or any(kind not in SCOPE_KINDS or level not in SCOPE_LEVELS
                                                  for kind, level in scope.items()):
                raise Refused("invalid", "scope maps decision kinds to decide, tell or ask")
            for kind, level in scope.items():
                if charter["scope"].get(kind) != level:
                    charter["scope"][kind] = level
                    label = next(entry[1] for entry in defaults.SCOPE_LABELS if entry[0] == kind)
                    said.append(f"decision scope: {label.lower()} → {level}")
        if not said:
            return {"version": charter["version"]}
        charter["version"] += 1
        charter["written_by"] = self.actor
        self.snapshot("charter", "main")
        for text in said:
            self.log(self.who_source(), 0, f"{text} (charter v{charter['version']})", "info")
        return {"version": charter["version"]}

    def op_epic_decompose(self, args: dict) -> dict:
        epic = self.resolve_item(text_arg(args, "epic"))
        if self.items[epic].kind != "epic":
            raise Refused("invalid", f"{self.title(epic)} is not an epic")
        proposal = self.decompose(epic, author="orchestrator")
        if proposal is None:
            gaps = self.gaps(epic)
            text = ("The remaining gaps belong to tasks that already exist but have no acceptance criteria yet: "
                    + "; ".join(criterion.text for criterion in gaps) + ". Write their criteria rather than adding "
                    "tasks.") if gaps else "Nothing to propose: every criterion is covered."
            return {"proposal": None, "text": text}
        self.log("orchestrator", 0, f"proposed {len(proposal['operations'])} child task(s) for {self.title(epic)}",
                 "info", subject=epic)
        return {"proposal": proposal["id"]}

    # ---- views
    def op_view_place(self, args: dict) -> dict:
        view_type = text_arg(args, "type")
        if view_type not in VIEW_TYPES:
            raise Refused("invalid", "type is one of " + ", ".join(VIEW_TYPES))
        title = (args.get("title") or {"swimlanes": "Tasks by epic", "board": "Tasks by status", "table": "All tasks",
                                       "progress": "Progress", "metric": "Waiting on you", "attention": "Needs you",
                                       "doc": "Spec", "agents": "Agents", "charter": "Charter", "note": "Note"}[view_type])
        options = dict(VIEW_DEFAULTS[view_type])
        for key, value in (args.get("options") or {}).items():
            if key in VIEW_TYPES[view_type] and value in VIEW_TYPES[view_type][key]:
                options[key] = value
        identity = self.new_id("view")
        personal = self.actor if args.get("personal") else None
        if "x" in args and "y" in args:
            x, y = float(args["x"]), float(args["y"])
        else:  # beside the workflow board, stacked, when the client gave no place (an adopted proposal)
            columns = len(self.workflow()["stages"]) + 1
            x = 60 + 126 + columns * 274 - 24 + 80
            y = 460 + 60 * (len(self.all("view")) % 8)
        self.put("view", identity, {"id": identity, "type": view_type, "title": str(title)[:80], "options": options,
                                    "code": view_code(view_type, str(title)[:80], options), "version": 1,
                                    "personal": personal, "text": "", "written_by": self.actor, "x": x, "y": y})
        self.snapshot("view", identity)
        self.log(f"view {view_type} v1", 0, f"{self.who()} added a {view_type} view; it reads records and changes "
                 "nothing", "info")
        return {"view": identity}

    def op_view_configure(self, args: dict) -> dict:
        view = self.need("view", text_arg(args, "view"), "view")
        if "text" in args:
            if not isinstance(args["text"], str) or len(args["text"]) > 8000:
                raise Refused("invalid", "a note's text is a string under 8,000 characters")
            view["text"] = args["text"]
            self.presentation.add(("view", view["id"]))
        if "at" in args and isinstance(args["at"], dict):
            view["x"], view["y"] = float(args["at"].get("x", view["x"])), float(args["at"].get("y", view["y"]))
            self.presentation.add(("view", view["id"]))
        options = args.get("options")
        if options:
            allowed = VIEW_TYPES[view["type"]]
            changed = []
            for key, value in options.items():
                if key not in allowed or value not in allowed[key]:
                    raise Refused("invalid", f"{key} takes " + ", ".join(allowed.get(key, ())))
                if view["options"].get(key) != value:
                    view["options"][key] = value
                    changed.append(f"{key} → {value}")
            if changed:
                titles = {"swimlanes": lambda: f"Tasks by {view['options']['rows']}" if "rows" in options else None,
                          "board": lambda: f"Tasks by {view['options']['group']}",
                          "metric": lambda: view["options"]["metric"].capitalize(),
                          "doc": lambda: {"spec": "Spec", "report": "Status report",
                                          "north star": "North star"}[view["options"]["doc"]]}
                title = titles.get(view["type"], lambda: None)()
                if title:
                    view["title"] = title
                view["code"] = view_code(view["type"], view["title"], view["options"],
                                         [line.text for line in compile_view(view["code"]).guidance()])
                view["version"] += 1
                self.presentation.discard(("view", view["id"]))
                self.snapshot("view", view["id"])
                self.log(f"view {view['type']} v{view['version']}", 0, f"“{view['title']}”: " + ", ".join(changed), "info")
        return {"view": view["id"], "version": view["version"]}

    def op_view_remove(self, args: dict) -> dict:
        view = self.need("view", text_arg(args, "view"), "view")
        self.drop("view", view["id"])
        for key in [key for key, entry in self.all("context").items() if entry["kind"] == "view" and entry["doc"] == view["id"]]:
            self.drop("context", key)
        self.log(f"view {view['type']}", 0, f"{self.who()} removed “{view['title']}”", "info")
        return {}

    # ---- context
    def op_context_add(self, args: dict) -> dict:
        doc = args.get("doc")
        if not isinstance(doc, dict) or doc.get("kind") not in ("doc", "view") or not isinstance(doc.get("id"), str):
            raise Refused("invalid", "doc names a document (spec, star, report) or a note or document view")
        region = self.resolve_region(args.get("region"))
        compiled = self.compiled("region", region["id"])
        if region.get("level") != "enforced" or not compiled.has("enter", "context_add"):
            raise Refused("wrong_kind", f"{region['name']} does not add documents to agents' context.",
                          source=self.source("region", region, 0))
        key = f"{doc['kind']}:{doc['id']}"
        title = doc.get("title") if isinstance(doc.get("title"), str) else doc["id"]
        line = next(line.n for line in compiled.ops("enter") if line.op == "context_add")
        if self.get("context", key) is None or self.get("context", key)["region"] != region["id"]:
            self.put("context", key, {"id": key, "kind": doc["kind"], "doc": doc["id"], "title": title[:120],
                                      "region": region["id"], "added_by": self.actor, "time": iso(self.now)})
            self.log(self.label("region", region), line, f"add to context: every agent in the space now reads “{title}”",
                     "op")
        return {"context": key}

    def op_context_remove(self, args: dict) -> dict:
        doc = args.get("doc")
        key = f"{doc.get('kind')}:{doc.get('id')}" if isinstance(doc, dict) else None
        entry = self.get("context", key) if key else None
        if entry is None:
            return {}
        region = self.get("region", entry["region"])
        self.drop("context", key)
        line = 0
        if region is not None:
            line = next((line.n for line in self.compiled("region", region["id"]).ops("exit")
                         if line.op == "context_remove"), 0)
        self.log(self.label("region", region) if region else "canvas", line,
                 f"remove from context: agents stop reading “{entry['title']}”", "op")
        return {}

    # ---- reader, pages, notes, settings
    def op_reader_mark_seen(self, args: dict) -> dict:
        self.put("seen", self.actor, {"id": self.actor, "seq": self.last_seq, "time": iso(self.now)})
        self.presentation.add(("seen", self.actor))
        return {"seq": self.last_seq}

    def op_page_update(self, args: dict) -> dict:
        markdown = text_arg(args, "markdown", limit=20000)
        page = self.get("page", "main")
        if page is None:
            page = self.put("page", "main", {"id": "main", "markdown": "", "version": 0})
        base = args.get("base")
        if base is not None and base != page["version"]:
            raise Refused("version_conflict", f"the page is at v{page['version']}, newer than v{base}",
                          source={"object": "page", "version": page["version"], "line": 0}, current=deepcopy(page))
        page["markdown"] = markdown
        page["version"] += 1
        self.log(self.who_source(), 0, "reading page rearranged", "info")
        return {"version": page["version"]}

    def op_spec_update(self, args: dict) -> dict:
        text = text_arg(args, "text", limit=8000)
        spec = self.get("spec", "main") or self.put("spec", "main", {"id": "main", "text": "", "version": 1})
        spec["text"] = text
        spec["version"] += 1
        self.log(f"spec v{spec['version']}", 0, f"{self.who()} revised the spec", "info")
        return {"version": spec["version"]}

    def op_note_dismiss(self, args: dict) -> dict:
        self.drop("note", text_arg(args, "id"))
        return {}

    def op_agent_configure(self, args: dict) -> dict:
        agent = text_arg(args, "agent", limit=40)
        if not re.fullmatch(r"[\w-]+", agent):
            raise Refused("invalid", "an agent name is letters, digits, - and _")
        settings = self.get("settings", "main") or self.put("settings", "main", {"id": "main", "agents": {}, "allow": []})
        if args.get("remove"):
            settings["agents"].pop(agent, None)
            self.log(self.who_source(), 0, f"{agent} is no longer set up to run work here", "info")
            return {"agents": settings["agents"]}
        mode = args.get("mode") or "dispatch"
        if mode not in ("dispatch", "simulate"):
            raise Refused("invalid", "mode is dispatch or simulate")
        entry = {"mode": mode}
        if mode == "dispatch":
            entry["host"] = text_arg(args, "host", limit=200)
            entry["cwd"] = text_arg(args, "cwd", limit=1000)
            runtime = args.get("runtime") or (agent if agent in ("claude", "codex") else None)
            if runtime not in ("claude", "codex"):
                raise Refused("invalid", "runtime is claude or codex")
            entry["runtime"] = runtime
            for name in ("permission", "model"):
                if args.get(name):
                    entry[name] = text_arg(args, name, limit=200)
        else:
            seconds = args.get("seconds", 20)
            if not isinstance(seconds, int) or not 1 <= seconds <= 3600:
                raise Refused("invalid", "seconds is between 1 and 3600")
            entry["seconds"] = seconds
        settings["agents"][agent] = entry
        where = f"on {entry['host']} in {entry['cwd']}" if mode == "dispatch" else f"as a {entry['seconds']}s simulation"
        self.log(self.who_source(), 0, f"{agent} runs work here {where}", "info")
        return {"agents": settings["agents"]}

    def op_tick(self, args: dict) -> dict:
        return self.tick()

    # ---- the agent's brief
    def brief(self, run_id: str) -> str:
        """The step an agent run receives: the work, its criteria, and every line of guidance with its source."""
        run = self.need("run", run_id, "run")
        identity = run["item"]
        item = self.items[identity]
        state = self.state(identity)
        epic = self.epic_of(identity)
        lines = [f"You are the {run['role']} for the work item “{item.title}” (work item {identity}, "
                 f"project {self.space}).", f"Goal: {item.goal}"]
        criteria = self.criteria.get(identity, [])
        if criteria:
            lines += ["", "Acceptance criteria Fleet will hold it to:"] + [f"- {criterion.text}" for criterion in criteria]
        if run["role"] == "builder":
            lines += ["", "Make the change in this checkout and commit it on a branch. When the revision is ready for "
                      "review, summarise what changed and how each criterion is met."]
        elif run["role"] == "tester":
            builder = state.get("builder")
            lines += ["", f"Test the current revision independently{f' of the builder ({builder})' if builder else ''}. "
                      "Run the checks the criteria need on this revision and report each criterion as pass or fail. "
                      "If any check fails, finish with `FLEET_STATUS: failed — <which criterion and why>`."]
        else:
            lines += ["", "Propose child tasks that cover the uncovered criteria; for each, give a title and its "
                      "acceptance criteria."]
        cited = []
        stage = state.get("stage")
        if stage and stage in self.all("stage"):
            record = self.get("stage", stage)
            cited += [(f"stage {stage} v{record['version']}, line {line.n}", line.text)
                      for line in self.compiled("stage", stage).guidance()]
        region = self.region_of(identity)
        if region:
            cited += [(f"zone {region['name']} v{region['version']}, line {line.n}", line.text)
                      for line in self.compiled("region", region["id"]).guidance()]
        keys = [f"task:{identity}"] + ([f"epic:{epic}"] if epic else [])
        cited += [(f"guidance from {entry['author']} on the {entry['target'].split(':')[0]}", entry["text"])
                  for entry in self.all("guidance").values() if entry["target"] in keys]
        charter = self.get("charter", "main") or {}
        cited += [(f"charter v{charter.get('version')}, clause {index + 1}", clause["text"])
                  for index, clause in enumerate(charter.get("clauses", [])) if clause.get("kind") == "guidance"]
        if cited:
            lines += ["", "Guidance to follow. When a line changes what you do, cite its source in your report:"]
            lines += [f"- [{source}] {text}" for source, text in cited]
        rules = [clause for clause in charter.get("clauses", []) if clause.get("kind") == "enforced"]
        if charter.get("north_star") or rules or charter.get("scope"):
            lines += ["", f"Charter v{charter.get('version')}:"]
            if charter.get("north_star"):
                lines.append(f"North star: {charter['north_star']}")
            lines += [f"Rule in force: {clause['text']} ({clause['rule']})" for clause in rules]
            labels = {entry[0]: entry[1] for entry in defaults.SCOPE_LABELS}
            levels = {"decide": "decide yourself", "tell": "decide, then tell the user", "ask": "ask the user first"}
            lines += [f"{labels[kind]}: {levels[level]}" for kind, level in charter.get("scope", {}).items()]
            lines.append("Rules in force outrank the decision scope. To ask, end with "
                         "`FLEET_STATUS: blocked — <your question>`.")
        context = list(self.all("context").values())
        if context:
            spec = self.get("spec", "main") or {}
            lines += ["", "Context every agent in this space reads:"]
            for entry in context:
                if entry["kind"] == "doc" and entry["doc"] == "spec" and spec.get("text"):
                    lines.append(f"- Spec v{spec['version']}: {spec['text']}")
                elif entry["kind"] == "doc" and entry["doc"] == "star":
                    lines.append(f"- North star: {charter.get('north_star', '')}")
                elif entry["kind"] == "view":
                    view = self.get("view", entry["doc"]) or {}
                    lines.append(f"- {entry['title']}: {view.get('text') or ''}".rstrip(": "))
                else:
                    lines.append(f"- {entry['title']}")
        return "\n".join(lines)

    # ---- words
    def who(self) -> str:
        return "you" if is_person(self.actor) else self.actor

    def who_source(self) -> str:
        return "you" if is_person(self.actor) else self.actor

    def next_text(self, identity: str, *, short: bool) -> str:
        state = self.state(identity)
        status = self.status(identity)
        region = self.region_of(identity)
        run = self.active_run(identity)
        flow = self.flow(state)
        if status == "done":
            return "Finished" if short else "Finished. Nothing more will run on it."
        if status == "queued":
            reason = (run or {}).get("queue_reason") or "Queued"
            if short:
                return reason
            return (f"Waiting to start: the {run['role']} is queued. {reason}. It is in band "
                    f"{state.get('band') or 'later'}.")
        if status == "struggling":
            return ("Struggling: commands refused" if short else
                    f"Struggling: its commands keep being refused ({(run or {}).get('excerpt') or ''}). "
                    "Allow them or reassign it.")
        if status == "blocked":
            return ("Blocked: needs you" if short else
                    f"Blocked: {(run or {}).get('excerpt') or 'it needs a permission or an answer'}. Answer it from "
                    "Needs you or the session.")
        if status == "paused":
            by = state.get("paused_by") or ""
            if by.startswith("zone ") and region:
                return (f"Paused by {region['name']}" if short else
                        f"Paused by the {region['name']} region's code. Drag it out of the region to resume it.")
            if by == "budget":
                return "Paused: budget spent" if short else "Paused because it spent its budget. Raise it or resume."
            return "Paused by you" if short else "Paused by you. Resume it from its session."
        if not state.get("stage"):
            first = self.stage_name(flow[0])
            return (f"Not started: drag into {first}" if short else
                    f"Not in the workflow yet. Drag it into {first} to start it.")
        if status == "working":
            agent = f"{run['role']} {run.get('agent')}"
            return agent if short else (f"The {run['role']} ({run.get('agent')}) is working. When it finishes, "
                                        + ("a revision is submitted" if run["role"] == "builder" else "test evidence is captured")
                                        + " and the stage's exit code is checked.")
        check = self.when_ok(identity)
        following = flow[flow.index(state["stage"]) + 1] if state["stage"] in flow[:-1] else "done"
        if check["ok"]:
            return (f"Moving to {self.stage_name(following)}" if short else
                    f"Its exit conditions are met. It moves to {self.stage_name(following)} on the next tick.")
        last = next((run for run in sorted(self.runs_for(identity, active=False), key=lambda r: r["queued_at"], reverse=True)), None)
        if last and last["state"] == "failed":
            return (f"{last['role'].capitalize()} run failed" if short else
                    f"The last {last['role']} run failed ({last.get('outcome')}). Retry it from the task, or reassign it.")
        if status == "waiting-you":
            return ("Needs your approval" if short else
                    f"Waiting for your approval. Approving it satisfies “{check.get('text')}” and moves it to "
                    f"{self.stage_name(following)}.")
        if status == "waiting-criteria":
            return ("Needs acceptance criteria" if short else
                    f"Waiting for acceptance criteria. Write them in the spec and it moves to {self.stage_name(following)}.")
        if not check.get("text"):
            return "Waiting" if short else "Waiting for the workflow."
        return (f"Waiting: {check['text']}" if short else
                f"Waiting until “{check['text']}” ({check['src']}, line {check['line']}).")
