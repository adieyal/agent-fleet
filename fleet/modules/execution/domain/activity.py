"""The deck's event-to-activity vocabulary, shared with recorded runs."""

import re


ACTION_FRESHNESS_SECONDS = 20
DOC_FILE = re.compile(r"\.(md|mdx|markdown|rst|txt)$", re.I)


def shell_activity(command: str) -> str:
    wrapped = re.fullmatch(r"\s*(?:ba|z)?sh\s+-l?c\s+(['\"])([\s\S]*)\1\s*", command)
    if wrapped:
        return shell_activity(wrapped[2])
    parts = [part.strip() for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command) if part.strip()]
    command = next((part for part in parts if not re.match(r"^(cd|export|source|set|\.)\b", part)),
                   parts[0] if parts else "")
    words = command.split()
    while words:
        first = words[0]
        if re.match(r"^\w+=", first) or first in ("sudo", "exec", "nice", "time", "command", "env"):
            words = words[1:]
        elif first == "timeout":
            words = words[2:] if len(words) > 1 and re.match(r"\d", words[1]) else words[1:]
        elif words[:2] == ["uv", "run"] or (re.fullmatch(r"python[\d.]*", first) and words[1:2] == ["-m"]):
            words = words[2:]
        elif first in ("npx", "pnpx", "bunx"):
            words = words[2:] if words[1:2] == ["-y"] else words[1:]
        else:
            break
    if not words:
        return "type"
    prog = words[0].split("/")[-1]
    if prog == "git":
        sub = next((word for word in words[1:] if not word.startswith("-")), "")
        if sub in ("commit", "push"):
            return "ship"
        return "review" if sub in ("diff", "log", "show", "blame", "status") else "type"
    if prog in ("pytest", "vitest", "jest", "mocha", "tox", "nox") or re.search(r"\b(test|pytest|vitest|jest)\b", " ".join(words[:3])):
        return "test"
    if prog in ("npm", "pnpm", "yarn", "bun"):
        if words[1:2] in (["i"], ["install"], ["ci"], ["add"]):
            return "build"
        script = " ".join(words[2:3] if words[1:2] == ["run"] else words[1:2])
        if "build" in script:
            return "build"
        if "lint" in script:
            return "test"
        if re.search(r"format|prettier", script):
            return "type"
        return "build" if re.search(r"dev|start|serve", script) else "type"
    groups = {
        "test": ("eslint", "ruff", "flake8", "pylint", "mypy", "pyright", "tsc"),
        "build": ("make", "docker", "docker-compose", "pip", "pip3", "uv", "poetry"),
        "search": ("ls", "tree", "find", "fd", "du", "stat", "grep", "rg", "ag", "psql", "mysql", "sqlite3"),
        "web": ("curl", "wget", "http", "ssh", "scp", "rsync", "gh", "fleet"),
        "wait": ("sleep", "ps", "top", "htop", "pgrep", "lsof", "ss", "netstat"),
    }
    return next((activity for activity, programs in groups.items() if prog in programs), "type")


def classify_activity(event: dict | None) -> str | None:
    if event is None:
        return None
    if event.get("kind") == "text":
        return "think"
    if event.get("kind") != "tool":
        return None
    name, tool = event.get("name"), event.get("tool")
    summary = event.get("summary") or ""
    if name in ("Bash", "shell"):
        return shell_activity(summary)
    if name in ("BashOutput", "AskUserQuestion"):
        return "wait"
    if name in ("Read", "Skill"):
        return "read"
    if name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return "doc" if DOC_FILE.search(summary) else "edit"
    if name == "apply_patch":
        return "doc" if summary and all(DOC_FILE.search(path) for path in summary.split(", ")) else "edit"
    if name in ("Grep", "Glob"):
        return "search"
    if name in ("WebFetch", "WebSearch", "web_search"):
        return "web"
    if name in ("TodoWrite", "TaskCreate", "TaskUpdate", "EnterPlanMode", "ExitPlanMode"):
        return "plan"
    if name in ("Task", "Agent"):
        return "delegate"
    if tool == "bash":
        return shell_activity(summary)
    if tool == "edit":
        return "doc" if DOC_FILE.search(summary) else "edit"
    return tool if tool in ("read", "search", "web", "think", "plan", "delegate") else "type"
