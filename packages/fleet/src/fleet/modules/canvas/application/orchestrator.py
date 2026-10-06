"""The orchestrator's replies to the message bar.

Replies are written from records, never invented: a question about status is
answered from the kernel's own reading, an instruction is stored as guidance on
its target, and a request becomes a proposal of kernel operations that you adopt
or discard. Nothing a message says changes a record until a proposal is adopted.
"""
from __future__ import annotations

import re

WHY = re.compile(r"why|stuck|slow|status|what.*doing|progress|update|how.*going|\?$")
RULE = re.compile(r"\buse\b|don.?t|do not|skip|instead|prefer|avoid|stop|make sure|always|never|should|must")


def reply(kernel, text: str, target: dict | None) -> dict:
    """{who, text, guidance: bool, proposals: [(desc, operations)]}."""
    lower = text.lower()
    is_why, is_rule = bool(WHY.search(lower)), bool(RULE.search(lower))
    if target and target.get("kind") in ("task", "session"):
        identity = target["id"]
        title = kernel.title(identity)
        run = kernel.active_run(identity)
        who = f"{run['role']} {run['agent']}" if run and run.get("agent") and run["state"] in ("running", "struggling", "blocked") else "orchestrator"
        if is_why:
            return {"who": who, "text": kernel.next_text(identity, short=False), "guidance": False, "proposals": []}
        if is_rule:
            return {"who": who, "guidance": True, "proposals": [],
                    "text": f"Understood. I've recorded that as guidance on “{title}”, attributed to you, and the "
                            "agent that owns it will cite it whenever it changes a step."}
        return {"who": who, "guidance": False, "proposals": [],
                "text": f"Noted on “{title}”. If it should change how the work is done, phrase it as an instruction "
                        "and I'll record it as guidance."}
    if target and target.get("kind") == "epic":
        epic = target["id"]
        gaps = kernel.gaps(epic)
        children = kernel.children(epic)
        done = sum(kernel.state(child).get("stage") == "done" for child in children)
        stage = kernel.epic_state(epic)["stage"]
        if is_why:
            body = (f"{kernel.title(epic)} is in {stage.capitalize()}. {done} of {len(children)} children are done, and "
                    + (f"{len(gaps)} of its criteria {'has' if len(gaps) == 1 else 'have'} no task covering it, so it "
                       "cannot be accepted yet." if gaps else "every criterion is covered."))
            proposals = [("Propose child tasks to cover the gaps", [{"op": "epic.decompose", "args": {"epic": epic}}])] if gaps else []
            return {"who": "orchestrator (epic owner)", "text": body, "guidance": False, "proposals": proposals}
        if is_rule:
            return {"who": "orchestrator (epic owner)", "guidance": True, "proposals": [],
                    "text": "Recorded as guidance on the epic. Its child tasks inherit it, and agents cite it when it "
                            "shapes a decision."}
        return {"who": "orchestrator (epic owner)", "text": f"Noted on {kernel.title(epic)}.", "guidance": False,
                "proposals": []}
    if target:
        return {"who": "orchestrator", "guidance": True, "proposals": [],
                "text": "Recorded as guidance on it. If it should be enforced rather than interpreted, edit its code, "
                        "or tell me what to change and I'll draft it."}
    return space_reply(kernel, text, lower, is_why)


def space_reply(kernel, text: str, lower: str, is_why: bool) -> dict:
    cards = kernel.card_ids()
    if re.search(r"cost|spend|expensive|money|budget", lower):
        return {"who": "orchestrator", "text": "Here is a view for that, sorted by spend.", "guidance": False,
                "proposals": [("Place a “What's costing the most” table on the canvas",
                               [{"op": "view.place", "args": {"type": "table", "title": "What's costing the most",
                                                              "options": {"columns": "cost", "sort": "cost"}}}])]}
    if re.search(r"\bpark|pause|hold|later\b", lower):
        words = [word for word in re.findall(r"[a-z]{4,}", lower)
                 if word not in ("park", "parked", "pause", "hold", "later", "everything", "waiting", "that", "with",
                                 "these", "those", "items", "tasks", "every", "thing", "anything")]
        picked = [identity for identity in cards if kernel.state(identity).get("stage") != "done"
                  and (not words or any(word in kernel.title(identity).lower() for word in words))]
        if "waiting" in lower:
            picked = [identity for identity in picked if kernel.status(identity).startswith("waiting")] or picked
        if not picked:
            return {"who": "orchestrator", "text": "Nothing matches that right now.", "guidance": False, "proposals": []}
        parked = next((region for region in kernel.all("region").values() if re.search("park", region["name"], re.I)), None)
        operations = []
        if parked is None:
            operations.append({"op": "region.create", "args": {"name": "Parked", "level": "enforced",
                                                               "rect": {"x": 1140, "y": 1320, "w": 440,
                                                                        "h": 140 + 120 * len(picked)}}})
        operations += [{"op": "region.enter", "args": {"region": parked["id"] if parked else "@Parked", "item": identity}}
                       for identity in picked]
        noun = "task" if len(picked) == 1 else "tasks"
        body = (f"That would park {len(picked)} {noun}: " + ", ".join(kernel.title(identity) for identity in picked) + "."
                + ("" if parked else " There is no Parked region yet, so I'd create one with the usual parked rules."))
        return {"who": "orchestrator", "text": body, "guidance": False,
                "proposals": [(("" if parked else "Create a Parked region and ") + f"move {len(picked)} {noun} into it",
                               operations)]}
    if re.search(r"epic", lower) and re.search(r"start|new|create|open", lower):
        match = re.search(r"for (.+)$", text, re.I)
        name = match[1].strip().rstrip(".?!") if match else "New epic"
        name = re.sub(r"^the ", "", name, flags=re.I)
        name = name[:1].upper() + name[1:]
        return {"who": "orchestrator", "guidance": False,
                "text": "I'd open it in Shape. It stays there until it has acceptance criteria and every one is "
                        "covered by a task.",
                "proposals": [(f"Create epic “{name}”", [{"op": "epic.create", "args": {"title": name}}])]}
    if re.search(r"swimlane|lanes|board|who.*working|show me", lower):
        return {"who": "orchestrator", "text": "A swimlane view would show that.", "guidance": False,
                "proposals": [("Place swimlanes (rows by epic, columns by stage)",
                               [{"op": "view.place", "args": {"type": "swimlanes", "title": "Tasks by epic"}}])]}
    if is_why:
        done = sum(kernel.state(identity).get("stage") == "done" for identity in cards)
        waiting = len(kernel.all("attn"))
        stuck = [identity for identity in cards if kernel.status(identity) in ("struggling", "blocked")]
        body = (f"{done} of {len(cards)} tasks are done, {waiting} {'decision is' if waiting == 1 else 'decisions are'} "
                "waiting for you" + (", and " + ", ".join(kernel.title(identity) for identity in stuck)
                                     + (" is" if len(stuck) == 1 else " are") + " stuck" if stuck else "") + ".")
        return {"who": "orchestrator", "text": body, "guidance": False,
                "proposals": [("Pin a status board where you're looking",
                               [{"op": "view.place", "args": {"type": "board", "title": "Tasks by status"}}])]}
    return {"who": "orchestrator", "guidance": False,
            "text": "I don't have a direct action for that, so I'd record it as a request and work it like any other item.",
            "proposals": [(f"Create a work item: “{text}”", [{"op": "item.create", "args": {"title": text[:200]}}])]}
