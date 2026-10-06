"""Starting code for a new space, and the interpretations offered for a named region."""
from __future__ import annotations

import re

STAGES = (
    ("plan", "Plan", 'stage plan\n  on enter:\n    assign owner "planner"\n  exit when:\n    spec has acceptance criteria'),
    ("implement", "Implement", "stage implement\n  on enter:\n    dispatch builder\n  exit when:\n    revision submitted"),
    ("approve", "Approve", 'stage approve\n  on enter:\n    ask you "Approve {item}?"\n  exit when:\n    you approve'),
)
TEST_STAGE = ("test", "Test", 'stage test\n  on enter:\n    dispatch tester independent of builder\n  exit when:\n'
              '    evidence "tests pass" on current revision\n  on fail:\n    send back to implement')
SCHEDULE = ("schedule v1\n  capacity:\n    claude: 2 runs\n    codex: 1 run\n  bands:\n    Now: limit 2 items\n"
            "  order:\n    band Now, then Next, then Later\n    then oldest first\n  respect:\n    dependencies\n"
            "    tester independent of builder")
EPIC_WORKFLOW = ('epic workflow v1\nstage shape\n  exit when:\n    every criterion covered by a child\nstage deliver\n'
                 '  on enter:\n    dispatch children\n  exit when:\n    all children in done\n'
                 '    every criterion covered by a child\nstage accept\n  on enter:\n    ask you "Accept {item}?"\n'
                 '  exit when:\n    you approve')
INBOX = 'zone "Inbox"\n  agents:\n    place new items here\n  on enter:\n    notify you "New in Inbox: {item}"'
PAGE = ("# {name}\nWhat changed, what needs you, and how it is going.\n::since-last-visit\n::needs-you\n"
        "::status-report\n::epics\n::charter\n::spec")
SCOPE = {"impl": "decide", "deps": "tell", "ops": "decide", "destructive": "ask", "interface": "ask",
         "arch": "ask", "scope": "ask", "process": "ask"}
SCOPE_LABELS = (
    ("impl", "Implementation inside a module", "naming, refactors, test structure"),
    ("deps", "Dependencies and packaging", "pins, wheels, package layout"),
    ("ops", "Routine operations", "rebuilds, restarts, cache reuse"),
    ("destructive", "Destructive or irreversible actions", "deleting images or data, force-push"),
    ("interface", "Interfaces between owners", "public APIs, payloads, contracts"),
    ("arch", "Architecture and deployment", "service boundaries, topology, rollback"),
    ("scope", "Scope and priorities", "milestones, what to do next"),
    ("process", "Process and rules", "gates, allowlists, budgets"),
)
# Kernel rules a constitution clause can be compiled into. They outrank any decision scope.
RULES = {
    "Deleting images needs you": "destructive",
    "Matching results need you": "interface",
    "Architecture drift": "arch",
    "Budgets need you": "process",
}
EPIC_STAGES = ("shape", "deliver", "accept", "done")


def zone_code(name: str) -> str:
    """The orchestrator's reading of a region's name: code you adopt, edit or discard."""
    lower = name.lower()
    if re.search(r"^now$|^doing$|in progress", lower):
        return (f'zone "{name}"\n  capacity:\n    limit 2 items\n  on enter:\n    start if not started\n'
                '    set priority high\n  on exit:\n    set priority normal\n  agents:\n'
                '    pull from "Next" when there is room\n    work these before anything else')
    if re.search(r"^next$|up next|queue", lower):
        return f'zone "{name}"\n  on enter:\n    pause runs\n  on exit:\n    resume runs\n  agents:\n    may propose entry'
    if re.search(r"context|read first|background|reference", lower):
        return (f'zone "{name}"\n  scope:\n    agents in this space\n  capacity:\n    accept documents only\n'
                '  on enter:\n    add to context\n  on exit:\n    remove from context')
    if re.search(r"park|later|hold|not now|someday|backlog|wait", lower):
        return (f'zone "{name}"\n  on enter:\n    pause runs\n    set budget 0\n    notify you "Parked {{item}}"\n'
                '  on exit:\n    require replan if parked longer than 14 days\n    resume runs\n  agents:\n'
                '    may propose entry\n    may not move items out\n'
                '    revisit weekly and say why each item is still parked')
    if re.search(r"urgent|today|hot|priority|asap|first", lower):
        return (f'zone "{name}"\n  on enter:\n    set priority high\n    notify you "{{item}} marked {name}"\n'
                '  agents:\n    work these before anything else')
    return f'zone "{name}"\n  meaning:\n    items here are “{name}”; interpret from the label and ask me if unsure'


PRESETS = {
    "nownext": (("Now", (540, 330)), ("Next", (300, 520))),
    "context": (("Context for agents", (640, 420)),),
}
