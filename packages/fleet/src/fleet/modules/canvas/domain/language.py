"""The Fleet decision language, vocabulary v1.

Every snippet is a header line followed by sections and lines. Each line is read
in exactly one way: compiled (the kernel acts on it), guidance (an agent
interprets and cites it) or off (the object is a label). Unrecognised lines are
never errors; they become guidance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVELS = ("enforced", "guidance", "label")
MARKERS = {"compiled": "✓", "guidance": "~", "off": "·"}
BANDS = ("now", "next", "later")
PRIORITIES = ("high", "normal", "low")
SCOPE_KINDS = ("impl", "deps", "ops", "destructive", "interface", "arch", "scope", "process")
SCOPE_LEVELS = ("decide", "tell", "ask")

# Section names as written, and the key the kernel files their lines under.
SECTIONS = {"on enter": "enter", "on exit": "exit", "exit when": "when", "on fail": "fail",
            "capacity": "capacity", "scope": "scope", "agents": "agents", "meaning": "meaning",
            "bands": "bands", "order": "order", "respect": "respect"}
SECTION_NAMES = {key: name for name, key in SECTIONS.items()}
# Which sections each kind of object may hold; anything else is kept as guidance.
OBJECT_SECTIONS = {
    "stage": ("enter", "when", "fail", "agents", "meaning"),
    "zone": ("enter", "exit", "capacity", "scope", "agents", "meaning"),
    "epic": ("enter", "when", "meaning"),
    "schedule": ("capacity", "bands", "order", "respect", "meaning"),
}


@dataclass(frozen=True)
class Operation:
    name: str
    pattern: re.Pattern
    sections: tuple[str, ...]
    effect: str
    objects: tuple[str, ...] = ("stage", "zone")


def _op(name, pattern, sections, effect, objects=("stage", "zone")):
    return Operation(name, re.compile(pattern + r"\Z"), sections, effect, objects)


# The operations the v1 kernel compiles; `{item}` in a quoted message is the item's title.
OPERATIONS = (
    _op("assign", r'assign owner "(.+)"', ("enter",), "Sets the item's owner"),
    _op("dispatch_builder", r"dispatch builder", ("enter",), "Queues a builder run for the scheduler"),
    _op("dispatch_tester", r"dispatch tester independent of builder", ("enter",),
        "Queues a test run that must go to a different agent from the builder"),
    _op("dispatch_tester_any", r"dispatch tester", ("enter",), "Queues a test run for the scheduler"),
    _op("dispatch_children", r"dispatch children", ("enter",), "Lets the epic's children be dispatched", ("epic",)),
    _op("dispatch_decomposer", r"dispatch decomposer", ("enter",),
        "Asks an agent to propose child tasks covering uncovered criteria", ("epic",)),
    _op("start_if_not_started", r"start if not started", ("enter",),
        "Enters the first workflow stage if the item has no stage"),
    _op("ask", r'ask you "(.+)"', ("enter",), "Raises an approval in Needs you and waits", ("stage", "zone", "epic")),
    _op("notify", r'notify you "(.+)"', ("enter", "exit"), "Adds a note under From your rules", ("stage", "zone", "epic")),
    _op("pause", r"pause runs", ("enter", "exit"), "Pauses the item's runs; spend stops"),
    _op("resume", r"resume runs", ("enter", "exit"), "Resumes a paused run where it was"),
    _op("budget", r"set budget (\d+(?:\.\d+)?)", ("enter",), "Sets the item's spend ceiling"),
    _op("priority", r"set priority (high|normal|low)", ("enter", "exit"), "Sets priority; high-priority runs are favoured"),
    _op("replan_after", r"require replan if parked longer than (\d+) days?", ("exit",),
        "Forces a re-plan when an item has been held too long"),
    _op("send_back", r"send back to ([\w-]+)", ("fail",), "Returns the item to an earlier stage"),
    _op("has_criteria", r"spec has acceptance criteria", ("when",), "True when the item has criteria in the spec"),
    _op("submitted", r"revision submitted", ("when",), "True when the builder has submitted a revision"),
    _op("evidence", r'evidence "(.+)" on current revision', ("when",),
        "True only for evidence captured on the current revision"),
    _op("approved", r"you approve", ("when",), "True after you approve in Needs you", ("stage", "epic")),
    _op("covered", r"every criterion covered by a child", ("when",),
        "True when each epic criterion has a child with criteria covering it", ("epic",)),
    _op("children_done", r"all children in done", ("when",), "True when every child is Done", ("epic",)),
    _op("limit", r"limit (\d+) items?", ("capacity",), "Refuses entry when n unfinished items are already inside", ("zone",)),
    _op("documents_only", r"accept documents only", ("capacity",), "Refuses tasks; accepts documents and notes", ("zone",)),
    _op("scope_space", r"agents in this space", ("scope",), "The region's effects apply to every agent in the space", ("zone",)),
    _op("context_add", r"add to context", ("enter",), "Every agent in the space reads the document", ("zone",)),
    _op("context_remove", r"remove from context", ("exit",), "Agents stop reading it", ("zone",)),
    _op("pull", r'pull from "(.+)" when there is room', ("agents",),
        "The orchestrator moves the oldest item across when capacity allows", ("zone",)),
    _op("placement", r"place new items here", ("agents",), "New agent-created items land in this region", ("zone",)),
    _op("may_propose", r"may propose entry", ("agents",), "Agents may suggest moving items in; you decide", ("zone",)),
    _op("no_exit", r"may not move items out", ("agents",), "Agents cannot remove items from the region", ("zone",)),
)
OPERATIONS_BY_NAME = {operation.name: operation for operation in OPERATIONS}

VIEW_TYPES = {
    "swimlanes": {"rows": ("epic", "priority", "agent", "owner", "status", "region"),
                  "columns": ("stage", "status"), "filter": ("all", "active", "needs me"),
                  "show": ("progress", "next step", "both")},
    "board": {"group": ("status", "stage", "epic")},
    "table": {"columns": ("compact", "detailed", "cost"), "sort": ("stage", "status", "progress", "cost")},
    "progress": {"include": ("active", "all")},
    "metric": {"metric": ("waiting on you", "done", "working", "paused", "in test")},
    "doc": {"doc": ("spec", "report", "north star")},
    "agents": {"show": ("active", "all")},
    "attention": {}, "note": {}, "charter": {},
}
VIEW_DEFAULTS = {
    "swimlanes": {"rows": "epic", "columns": "stage", "filter": "active", "show": "both"},
    "board": {"group": "status"}, "table": {"columns": "detailed", "sort": "stage"},
    "progress": {"include": "active"}, "metric": {"metric": "waiting on you"}, "doc": {"doc": "spec"},
    "agents": {"show": "active"}, "attention": {}, "note": {}, "charter": {},
}
DIRECTIVES = ("since-last-visit", "needs-you", "status-report", "epics", "charter", "spec")

STAGE_HEADER = re.compile(r"stage ([a-z][\w-]*)\Z")
ZONE_HEADER = re.compile(r'zone "(.+)"\Z')
EPIC_HEADER = re.compile(r"epic workflow v(\d+)\Z")
VIEW_HEADER = re.compile(r'view (\w+) "(.*)"\Z')
SCHEDULE_HEADER = re.compile(r"schedule v(\d+)\Z")
SECTION_LINE = re.compile(r"(" + "|".join(SECTIONS) + r"):\Z")


class CodeInvalid(ValueError):
    """Code whose header is missing or changed; the only way a snippet fails to compile."""


@dataclass(frozen=True)
class Line:
    """One source line and how it is read."""
    n: int
    raw: str
    kind: str  # header, section, op, option, guide, blank, off
    section: str | None = None
    op: str | None = None
    args: tuple[str, ...] = ()
    stage: str | None = None  # the epic stage a line belongs to
    note: str | None = None

    @property
    def text(self) -> str:
        return self.raw.strip()

    @property
    def marker(self) -> str:
        return {"op": MARKERS["compiled"], "option": MARKERS["compiled"], "guide": MARKERS["guidance"],
                "off": MARKERS["off"]}.get(self.kind, "")

    def as_dict(self) -> dict:
        return {"n": self.n, "raw": self.raw, "kind": self.kind, "section": self.section, "op": self.op,
                "args": list(self.args), "stage": self.stage, "marker": self.marker, "note": self.note,
                "describe": describe(self) if self.op and self.kind in ("op", "guide", "off") else None}


@dataclass(frozen=True)
class Compiled:
    """A snippet as the kernel reads it."""
    object: str  # stage, zone, epic, view, schedule
    name: str  # stage id, zone name, view type or the version for schedule and epic workflow
    lines: tuple[Line, ...]
    level: str = "enforced"
    title: str | None = None  # a view's title
    options: dict = field(default_factory=dict)  # views: option -> value; schedule: parsed policy
    stages: tuple[str, ...] = ()  # epic workflow stage names in order

    def section(self, key: str, *, stage: str | None = None) -> list[Line]:
        """Operations and guidance in a section, in source order (an epic's for one of its stages)."""
        return [line for line in self.lines if line.section == key and line.kind in ("op", "guide", "option")
                and (stage is None or line.stage == stage)]

    def ops(self, key: str, *, stage: str | None = None) -> list[Line]:
        if self.level != "enforced":
            return []
        return [line for line in self.section(key, stage=stage) if line.kind == "op"]

    def guidance(self, key: str | None = None, *, stage: str | None = None) -> list[Line]:
        """Lines an agent interprets: unrecognised ones, or every line when the object is guidance only."""
        if self.level == "label":
            return []
        return [line for line in self.lines if line.kind in ("guide", "op", "option")
                and (line.kind == "guide" or self.level == "guidance")
                and (key is None or line.section == key) and (stage is None or line.stage == stage)]

    def has(self, key: str, op: str) -> bool:
        return any(line.op == op for line in self.ops(key))

    def counts(self) -> tuple[int, int]:
        compiled = sum(line.kind in ("op", "option") for line in self.lines)
        guided = sum(line.kind == "guide" for line in self.lines)
        return compiled, guided

    def as_dict(self) -> dict:
        compiled, guided = self.counts()
        return {"object": self.object, "name": self.name, "level": self.level, "title": self.title,
                "options": self.options, "stages": list(self.stages), "compiled": compiled, "guided": guided,
                "lines": [line.as_dict() for line in self.lines]}


def header_of(code: str) -> str:
    for raw in str(code or "").split("\n"):
        if raw.strip():
            return raw.strip()
    return ""


def _match_operation(text: str, section: str | None, object_kind: str):
    for operation in OPERATIONS:
        match = operation.pattern.match(text)
        if match:
            if object_kind not in operation.objects:
                return None, f"{operation.name.replace('_', ' ')} is not valid in a {object_kind}"
            if section not in operation.sections:
                allowed = " or ".join(SECTION_NAMES[name] for name in operation.sections)
                return None, f"only valid under {allowed}"
            return (operation.name, match.groups()), None
    return None, None


def compile_code(code: str, *, level: str = "enforced") -> Compiled:
    """Read a stage, zone or epic workflow snippet. Raises CodeInvalid only for a bad header."""
    if level not in LEVELS:
        raise CodeInvalid(f"unknown level '{level}'; use enforced, guidance or label")
    head = header_of(code)
    if STAGE_HEADER.match(head):
        object_kind, name = "stage", STAGE_HEADER.match(head)[1]
    elif ZONE_HEADER.match(head):
        object_kind, name = "zone", ZONE_HEADER.match(head)[1]
    elif EPIC_HEADER.match(head):
        object_kind, name = "epic", EPIC_HEADER.match(head)[1]
    elif SCHEDULE_HEADER.match(head):
        return compile_schedule(code)
    elif VIEW_HEADER.match(head):
        return compile_view(code)
    else:
        raise CodeInvalid("the first line must be a header such as stage implement, zone \"Parked\", "
                          "epic workflow v1, schedule v1 or view board \"Title\"")
    allowed = OBJECT_SECTIONS[object_kind]
    lines, section, stage, seen_header, stages = [], None, None, False, []
    for index, raw in enumerate(str(code).split("\n"), start=1):
        text = raw.strip()
        if not text:
            lines.append(Line(index, raw, "blank"))
            continue
        if not seen_header:
            seen_header = True
            lines.append(Line(index, raw, "header"))
            continue
        if object_kind == "epic" and STAGE_HEADER.match(text):
            stage, section = STAGE_HEADER.match(text)[1], None
            stages.append(stage)
            lines.append(Line(index, raw, "header", stage=stage))
            continue
        section_match = SECTION_LINE.match(text)
        if section_match:
            key = SECTIONS[section_match[1]]
            if key in allowed:
                section = key
                lines.append(Line(index, raw, "section", section=key, stage=stage))
                continue
            section = "meaning"
            lines.append(Line(index, raw, "guide", section="meaning", stage=stage,
                              note=f"a {object_kind} has no {section_match[1]} section"))
            continue
        key = section or "meaning"
        found, problem = (None, None) if key == "meaning" else _match_operation(text, key, object_kind)
        if found:
            kind = "off" if level == "label" else "guide" if level == "guidance" else "op"
            lines.append(Line(index, raw, kind, section=key, op=found[0],
                              args=tuple(argument for argument in found[1] if argument is not None), stage=stage))
        else:
            lines.append(Line(index, raw, "off" if level == "label" else "guide", section=key, stage=stage,
                              note=problem))
    return Compiled(object_kind, name, tuple(lines), level, stages=tuple(stages))


def compile_schedule(code: str) -> Compiled:
    head = header_of(code)
    match = SCHEDULE_HEADER.match(head)
    if not match:
        raise CodeInvalid("the first line must stay: schedule vN")
    policy = {"capacity": {}, "bands": {}, "order": [], "dependencies": False, "independent_testers": False,
              "lines": {}}
    lines, section, seen_header = [], None, False
    for index, raw in enumerate(str(code).split("\n"), start=1):
        text = raw.strip()
        if not text:
            lines.append(Line(index, raw, "blank"))
            continue
        if not seen_header:
            seen_header = True
            lines.append(Line(index, raw, "header"))
            continue
        section_match = SECTION_LINE.match(text)
        if section_match and SECTIONS[section_match[1]] in OBJECT_SECTIONS["schedule"]:
            section = SECTIONS[section_match[1]]
            lines.append(Line(index, raw, "section", section=section))
            continue
        op, args = None, ()
        if section == "capacity" and (found := re.fullmatch(r"([\w-]+): (\d+) runs?", text)):
            op, args = "capacity", found.groups()
            policy["capacity"][found[1]] = int(found[2])
            policy["lines"]["capacity:" + found[1]] = index
        elif section == "bands" and (found := re.fullmatch(r"(Now|Next|Later): limit (\d+) items?", text, re.I)):
            op, args = "band_limit", found.groups()
            policy["bands"][found[1].lower()] = int(found[2])
            policy["lines"]["band:" + found[1].lower()] = index
        elif section == "order" and text == "band Now, then Next, then Later":
            op = "order_bands"
            policy["order"].append("bands")
            policy["lines"]["order"] = index
        elif section == "order" and text == "then oldest first":
            op = "order_oldest"
            policy["order"].append("oldest")
        elif section == "respect" and text == "dependencies":
            op = "respect_dependencies"
            policy["dependencies"] = True
            policy["lines"]["dependencies"] = index
        elif section == "respect" and text == "tester independent of builder":
            op = "respect_independence"
            policy["independent_testers"] = True
            policy["lines"]["independence"] = index
        if op:
            lines.append(Line(index, raw, "op", section=section, op=op, args=tuple(args)))
        else:
            lines.append(Line(index, raw, "guide", section=section or "meaning"))
    return Compiled("schedule", match[1], tuple(lines), options=policy)


def compile_view(code: str) -> Compiled:
    raw_lines = str(code).split("\n")
    head = header_of(code)
    match = VIEW_HEADER.match(head)
    if not match or match[1] not in VIEW_TYPES:
        raise CodeInvalid('the first line must be view TYPE "Title", with TYPE one of ' + ", ".join(VIEW_TYPES))
    view_type, allowed = match[1], VIEW_TYPES[match[1]]
    options = dict(VIEW_DEFAULTS[view_type])
    lines, seen_header = [], False
    for index, raw in enumerate(raw_lines, start=1):
        text = raw.strip()
        if not text:
            lines.append(Line(index, raw, "blank"))
            continue
        if not seen_header:
            seen_header = True
            lines.append(Line(index, raw, "header"))
            continue
        option = re.fullmatch(r"(\w+):\s*(.+)", text)
        if option and option[1] in allowed and option[2] in allowed[option[1]]:
            options[option[1]] = option[2]
            lines.append(Line(index, raw, "option", section="options", op=option[1], args=(option[2],)))
        else:
            note = None
            if option and option[1] in allowed:
                note = f"{option[1]} takes " + ", ".join(allowed[option[1]])
            lines.append(Line(index, raw, "guide", section="options", note=note))
    return Compiled("view", view_type, tuple(lines), title=match[2], options=options)


def view_code(view_type: str, title: str, options: dict, guidance: list[str] = ()) -> str:
    allowed = VIEW_TYPES[view_type]
    body = [f"  {name}: {options.get(name, VIEW_DEFAULTS[view_type].get(name))}" for name in allowed]
    return "\n".join([f'view {view_type} "{title}"'] + body + [f"  {line}" for line in guidance])


def describe(line: Line) -> str:
    """A compiled line in plain words, for cards and proposals."""
    arg = line.args[0] if line.args else ""
    words = {
        "assign": f"assign owner {arg}", "dispatch_builder": "dispatch a builder",
        "dispatch_tester": "dispatch an independent tester", "dispatch_tester_any": "dispatch a tester",
        "dispatch_children": "let its children be dispatched", "dispatch_decomposer": "ask an agent to propose children",
        "ask": "ask you to approve", "notify": "tell you", "pause": "pause the item's runs", "resume": "resume its runs",
        "budget": f"set its budget to ${arg}", "priority": f"mark it {arg} priority",
        "replan_after": f"require a re-plan after {arg} days", "may_propose": "agents may suggest moving items in",
        "no_exit": "agents may not move items out", "placement": "agents put new items here",
        "has_criteria": "the spec has acceptance criteria", "submitted": "a revision is submitted",
        "approved": "you approve", "evidence": f"evidence that {arg} on the current revision",
        "send_back": f"send it back to {arg}", "limit": f"hold at most {arg} unfinished items",
        "pull": f"agents pull the oldest item from {arg} when there is room",
        "start_if_not_started": "start it in the first stage if it hasn't started",
        "context_add": "every agent in the space reads it", "context_remove": "agents stop reading it",
        "documents_only": "accept documents and notes, not tasks", "scope_space": "applies to every agent in this space",
        "covered": "every criterion is covered by a child", "children_done": "all children are done",
    }
    return words.get(line.op or "", line.text)


def replace_header_version(code: str, version: int) -> str:
    """Schedules and epic workflows name their version; Fleet sets it on each compile."""
    lines = str(code).rstrip().split("\n")
    for index, raw in enumerate(lines):
        if raw.strip():
            lines[index] = re.sub(r"v\d+\s*$", f"v{version}", raw.rstrip())
            break
    return "\n".join(lines)


def parse_page(markdown: str) -> list[dict]:
    """A reading page: `# ` title, `::directive` components, and prose. Unknown directives become guidance."""
    blocks = []
    for index, raw in enumerate(str(markdown or "").split("\n"), start=1):
        text = raw.strip()
        if not text:
            continue
        if text.startswith("# "):
            blocks.append({"kind": "title", "text": text[2:].strip(), "n": index})
        elif (directive := re.fullmatch(r"::([\w-]+)", text)):
            known = directive[1] in DIRECTIVES
            blocks.append({"kind": "directive" if known else "guide", "directive": directive[1], "text": text,
                           "n": index})
        else:
            blocks.append({"kind": "text", "text": text, "n": index})
    return blocks
