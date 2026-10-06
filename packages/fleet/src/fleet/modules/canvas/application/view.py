"""The read model every canvas client renders: records plus what the kernel derives from them."""
from __future__ import annotations

from ..domain import defaults
from ..domain.language import DIRECTIVES, VIEW_TYPES, compile_code, compile_view, describe, parse_page
from .kernel import ACTIVE_RUN, BUSY_RUN, is_person, parse_time

TASK_STATUS = {"working": "Working", "idle": "Idle", "waiting-you": "Waiting on you",
               "waiting-criteria": "Waiting: no criteria", "paused": "Paused", "done": "Done",
               "struggling": "Struggling", "blocked": "Blocked", "queued": "Queued"}
EPIC_STATUS = {"shape": "Shaping", "deliver": "Delivering", "accept": "Waiting for your acceptance", "done": "Accepted"}


def run_view(engine, run: dict) -> dict:
    started, ended = parse_time(run.get("started_at")), parse_time(run.get("ended_at"))
    elapsed = int(((ended or engine.now) - started).total_seconds()) if started else None
    return {key: run.get(key) for key in ("id", "item", "role", "state", "agent", "builder", "queued_at", "queue_reason",
                                          "fleet_run", "host", "job", "error", "started_at", "ended_at", "outcome",
                                          "simulated", "excerpt", "refusals", "cost", "progress", "force_agent",
                                          "permit")} | {"elapsed": elapsed} | fleet_view(engine, run)


def fleet_view(engine, run: dict) -> dict:
    """What the real run on its host reports: its status, current activity and when it was last seen."""
    if not run.get("fleet_run"):
        return {"fleet": None}
    fleet = engine.ports.run(run["fleet_run"])
    if fleet is None:
        return {"fleet": None}
    stamp = lambda value: value.isoformat() if hasattr(value, "isoformat") else value
    return {"fleet": {"status": fleet.status, "reason": fleet.reason, "current_action": getattr(fleet, "current_action", None),
                      "last_observed": stamp(getattr(fleet, "last_observed", None)), "start": stamp(fleet.start),
                      "end": stamp(fleet.end), "runtime": fleet.runtime, "title": getattr(fleet, "title", None)}}


def item_view(engine, identity: str) -> dict:
    item = engine.items[identity]
    state = engine.state(identity)
    status = engine.status(identity)
    flow = engine.flow(state)
    stage = state.get("stage")
    if stage == "done":
        progress = 100
    elif stage in flow:
        progress = round(flow.index(stage) / max(1, len(flow) - 1) * 100)
    else:
        progress = 0
    run = engine.active_run(identity)
    runs = sorted(engine.runs_for(identity, active=False), key=lambda entry: entry["queued_at"], reverse=True)
    refs = []
    if stage and stage != "done" and stage in engine.all("stage"):
        record = engine.get("stage", stage)
        check = engine.when_ok(identity)
        refs.append({"kind": "stage", "id": stage, "label": engine.label("stage", record),
                     "note": "exit conditions met; advancing on the next tick" if check["ok"] else
                     f"waiting on line {check['line']}: {check['text']}" if check.get("line") else "checking"})
    region = engine.region_of(identity)
    if region:
        refs.append({"kind": "zone", "id": region["id"], "label": engine.label("region", region),
                     "note": "label only: no effect" if region.get("level") == "label" else
                     "its on-exit code runs if you drag the card out"})
    if state.get("pin"):
        refs.append({"kind": "workflow", "id": "main", "label": f"workflow v{state['pin']} (pinned)",
                     "note": "finishing under the version it started with"})
    epic = engine.epic_of(identity)
    keys = [f"task:{identity}"] + ([f"epic:{epic}"] if epic else [])
    guidance = [entry for entry in engine.all("guidance").values() if entry["target"] in keys]
    spent = round(sum(entry.get("cost") or 0 for entry in engine.runs_for(identity, active=False)), 2)
    badges = []
    if state.get("pin"):
        badges.append(f"finishing under v{state['pin']}")
    if region:
        badges.append(f"in {region['name']}")
    if state.get("priority") == "high":
        badges.append("priority high")
    if status == "paused" and state.get("budget") is not None:
        badges.append(f"budget ${state['budget']:g}")
    if run and run.get("simulated"):
        badges.append("simulated run")
    facts = state["facts"]
    evidence_current = {check: revision == facts.get("revision") for check, revision in facts.get("evidence", {}).items()}
    return {
        "id": identity, "title": item.title, "goal": item.goal, "kind": item.kind, "condition": item.condition,
        "epic": epic, "stage": stage, "band": state.get("band") or "later", "region": state.get("region"),
        "owner": state.get("owner"), "budget": state.get("budget"), "priority": state.get("priority"),
        "paused": state.get("paused"), "paused_by": state.get("paused_by"), "pin": state.get("pin"),
        "status": status, "status_label": TASK_STATUS[status], "next": engine.next_text(identity, short=True),
        "next_long": engine.next_text(identity, short=False), "progress": progress,
        "facts": {"submitted": bool(facts.get("submitted")), "revision": facts.get("revision"),
                  "evidence": evidence_current, "approved": bool(facts.get("approved")),
                  "has_criteria": engine.has_criteria(identity)},
        "criteria": [{"id": criterion.id, "text": criterion.text, "state": criterion.state}
                     for criterion in engine.criteria.get(identity, [])],
        "covers": state.get("covers", []), "builder": state.get("builder"),
        "run": run_view(engine, run) if run else None, "runs": [run_view(engine, entry) for entry in runs[:6]],
        "spent": spent, "refs": refs, "badges": badges,
        "waits_on": [first for first, waits in engine.depends() if waits == identity],
        "holds_up": [waits for first, waits in engine.depends() if first == identity],
        "guidance": [{"text": entry["text"], "author": entry["author"], "target": entry["target"],
                      "time": entry["time"]} for entry in guidance],
        "updated": item.updated.isoformat() if hasattr(item.updated, "isoformat") else item.updated,
    }


def epic_view(engine, identity: str) -> dict:
    item = engine.items[identity]
    state = engine.epic_state(identity)
    children = engine.children(identity)
    gaps = {criterion.id for criterion in engine.gaps(identity)}
    criteria = []
    for criterion in engine.criteria.get(identity, []):
        holders = [child for child in children if criterion.id in engine.state(child).get("covers", [])]
        criteria.append({"id": criterion.id, "text": criterion.text, "covered_by": holders,
                         "ok": criterion.id not in gaps})
    statuses = [engine.status(child) for child in children]
    done = statuses.count("done")
    working = sum(status in ("working", "queued") for status in statuses)
    waiting = sum(status in ("waiting-you", "waiting-criteria", "paused", "struggling", "blocked") for status in statuses)
    average = round(sum(100 if status == "done" else item_view_progress(engine, child) * 0.6
                        for child, status in zip(children, statuses)) / len(children)) if children else 0
    stage = state["stage"]
    if stage == "done":
        next_text = "Accepted. Nothing more will run under it."
    elif stage == "accept":
        next_text = "Every child is done and every criterion is covered. It is waiting for you to accept it."
    elif gaps:
        next_text = (f"{len(gaps)} of its criteria {'has' if len(gaps) == 1 else 'have'} no child task covering it. "
                     + ("It stays in Shape until they do." if stage == "shape" else "It cannot be accepted until they do.")
                     + " Ask an agent to propose tasks, or write criteria for the children that should cover them.")
    elif not engine.criteria.get(identity):
        next_text = "It has no acceptance criteria yet. Write them to start shaping it."
    elif children and all(status == "done" for status in statuses):
        next_text = "Moving to Accept on the next tick."
    else:
        next_text = f"Delivering: it moves to Accept when all {len(children)} children are done."
    guidance = [entry for entry in engine.all("guidance").values() if entry["target"] == f"epic:{identity}"]
    return {"id": identity, "title": item.title, "goal": item.goal, "ref": state.get("ref"), "color": state["color"],
            "stage": stage, "stage_label": EPIC_STATUS.get(stage, stage), "criteria": criteria, "children": children,
            "gaps": len(gaps), "done": done, "working": working, "waiting": waiting, "average": average,
            "next": next_text, "attention": sum(1 for record in engine.all("attn").values()
                                                if record.get("epic") == identity or
                                                (record.get("item") and engine.epic_of(record["item"]) == identity)),
            "guidance": [{"text": entry["text"], "author": entry["author"]} for entry in guidance]}


def item_view_progress(engine, identity: str) -> int:
    state = engine.state(identity)
    flow = engine.flow(state)
    stage = state.get("stage")
    return 100 if stage == "done" else round(flow.index(stage) / max(1, len(flow) - 1) * 100) if stage in flow else 0


def code_view(compiled) -> dict:
    return compiled.as_dict()


def read_model(engine, *, person: str, events: list[dict], last_seq: int, layout: dict, project_name: str,
               other_attention: list, versions: dict | None = None) -> dict:
    workflow = engine.get("workflow", "main")
    stages = []
    for stage_id in workflow["stages"] if workflow else []:
        record = engine.get("stage", stage_id)
        compiled = engine.compiled("stage", stage_id)
        exits = [describe(line) if line.kind == "op" else line.text for line in compiled.section("when")]
        stages.append(record | {"compiled": code_view(compiled), "exit_text": "exit when " + ", ".join(exits)
                                if exits else "no exit conditions", "label": engine.label("stage", record)})
    pinned = sorted({engine.state(identity)["pin"] for identity in engine.card_ids() if engine.state(identity).get("pin")})
    regions = []
    for region in engine.all("region").values():
        compiled = engine.compiled("region", region["id"])
        sub = None
        limit = next((line for line in compiled.ops("capacity") if line.op == "limit"), None)
        if limit is not None:
            held = sum(1 for identity in engine.card_ids() if engine.state(identity).get("region") == region["id"]
                       and engine.state(identity).get("stage") != "done")
            cap = int(limit.args[0])
            sub = {"text": f"{held} of {cap}" + (" · full" if held >= cap else f" · room for {cap - held}"),
                   "tone": "full" if held >= cap else "ok"}
        elif compiled.has("enter", "context_add"):
            inside = [entry["title"] for entry in engine.all("context").values() if entry["region"] == region["id"]]
            sub = {"text": "Agents read: " + " · ".join(inside) if inside else
                   "Drop documents or notes here; every agent in the space will read them", "tone": "context"}
        regions.append(region | {"compiled": code_view(compiled), "label": engine.label("region", region), "sub": sub,
                                 "accepts_documents": region.get("level") == "enforced" and compiled.has("enter", "context_add"),
                                 "items": [identity for identity in engine.card_ids()
                                           if engine.state(identity).get("region") == region["id"]]})
    schedule_record = engine.get("schedule", "main") or {"version": 1, "code": defaults.SCHEDULE, "written_by": "fleet"}
    compiled_schedule = engine.schedule()
    policy = compiled_schedule.options
    busy = {agent: sum(1 for run in engine.all("run").values() if run.get("agent") == agent and run["state"] in BUSY_RUN)
            for agent in policy["capacity"]}
    queue = sorted((run for run in engine.all("run").values() if run["state"] == "queued"),
                   key=lambda run: ({"now": 0, "next": 1, "later": 2}.get(engine.state(run["item"]).get("band"), 2),
                                    run["queued_at"]))
    epicflow = engine.get("epicflow", "main") or {"version": 1, "code": defaults.EPIC_WORKFLOW, "written_by": "fleet"}
    attention = []
    for record in engine.all("attn").values():
        attention.append({"id": record["id"], "fleet": record.get("fleet"), "kind": record["kind"], "text": record["text"],
                          "why": record["why"], "item": record.get("item"), "epic": record.get("epic") or
                          (engine.epic_of(record["item"]) if record.get("item") else None),
                          "ok": "Accept" if record["kind"] == "Accept" else "Allow for this run"
                          if record["kind"] == "Unblock" else "Approve",
                          "alt": "Open session" if record["kind"] == "Unblock" else "Send back", "canvas": True,
                          "answer": "canvas",
                          "time": record.get("time")})
    known = {record.get("fleet") for record in engine.all("attn").values()}
    for item in other_attention:
        if item.id in known or item.owner != "user" or item.state == "resolved":
            continue
        context = item.stream_context
        if item.refusals:
            answer = "refusal"
        elif item.questions or getattr(item, "at_terminal", False):
            answer = "terminal"
        elif context is not None and context.blocked_step:
            answer = "blocked"
        else:
            answer = "decision"
        attention.append({"id": item.id, "fleet": item.id, "kind": item.kind.capitalize(), "text": item.headline,
                          "answer": answer, "message": getattr(context, "message", None) if answer == "blocked" else None,
                          "why": f"{item.source} · {item.kind}", "item": item.work_item if item.work_item in engine.items else None,
                          "epic": None, "ok": item.options[0] if item.options else "Done",
                          "alt": item.options[1] if len(item.options) > 1 else None, "canvas": False,
                          "options": list(item.options), "time": item.last_seen.isoformat()
                          if hasattr(item.last_seen, "isoformat") else None})
    views = [view | {"compiled": code_view(compile_view(view["code"]))} for view in engine.all("view").values()
             if not view.get("personal") or view["personal"] == person]
    charter = engine.get("charter", "main") or {"version": 0, "north_star": "", "clauses": [], "scope": dict(defaults.SCOPE)}
    page = engine.get("page", "main") or {"markdown": defaults.PAGE.format(name=project_name), "version": 0}
    seen = (engine.get("seen", person) or {}).get("seq", 0)
    messages: dict[str, list] = {}
    for message in sorted(engine.all("message").values(), key=lambda entry: (entry["time"], entry.get("order", 0))):
        messages.setdefault(message["key"], []).append(message)
    guidance: dict[str, list] = {}
    for entry in engine.all("guidance").values():
        guidance.setdefault(entry["target"], []).append(entry)
    return {
        "space": engine.space, "name": project_name, "person": person, "now": engine.now.isoformat(), "seq": last_seq,
        "workflow": {"version": workflow["version"] if workflow else 0, "stages": stages, "pinned": pinned,
                     "flow": " → ".join([stage["name"] for stage in stages] + ["Done"])},
        "blocks": list(engine.all("block").values()),
        "regions": regions,
        "schedule": schedule_record | {"compiled": code_view(compiled_schedule), "slots": [
            {"agent": agent, "busy": busy[agent], "cap": cap, "ready": agent in engine.agents()}
            for agent, cap in policy["capacity"].items()], "limits": policy["bands"],
            "queue": [{"run": run["id"], "item": run["item"], "role": run["role"],
                       "band": engine.state(run["item"]).get("band") or "later", "why": run.get("queue_reason")}
                      for run in queue]},
        "epicflow": epicflow | {"compiled": code_view(engine.epic_compiled())},
        "epics": [epic_view(engine, identity) for identity in engine.epic_ids()],
        "items": [item_view(engine, identity) for identity in engine.card_ids()],
        "deps": [{"from": first, "to": waits} for first, waits in engine.depends()],
        "attention": attention,
        "notes": sorted(engine.all("note").values(), key=lambda note: note["time"], reverse=True)[:20],
        "views": views,
        "charter": charter, "scope_labels": [list(entry) for entry in defaults.SCOPE_LABELS],
        "rules": list(defaults.RULES),
        "page": page | {"blocks": parse_page(page["markdown"]), "directives": list(DIRECTIVES)},
        "spec": engine.get("spec", "main") or {"text": "", "version": 1},
        "context": list(engine.all("context").values()),
        "proposals": sorted(engine.all("proposal").values(), key=lambda entry: entry["time"], reverse=True)[:40],
        "messages": messages, "guidance": guidance,
        "log": events, "seen": seen,
        "settings": {"agents": engine.agents()},
        "layout": layout,
        "view_types": {name: {key: list(values) for key, values in options.items()} for name, options in VIEW_TYPES.items()},
        "presets": {key: [{"name": name, "w": size[0], "h": size[1]} for name, size in value]
                    for key, value in defaults.PRESETS.items()},
        "zone_colors": ["#5d8f84", "#6a9cc4", "#9b86d1", "#d47fa6", "#e3a76f", "#d9c25a", "#7fb069", "#e07a6a", "#8a979b"],
        "versions": versions or {},
    }
