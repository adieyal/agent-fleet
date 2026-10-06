"""Named opt-in Claude rule sets, expanded before durable dispatch."""
from fleet.errors import FleetError


REVIEW_RULES = (
    'Bash(cd:*)', 'Bash(pwd:*)', 'Bash(ls:*)', 'Bash(cat:*)',
    'Bash(head:*)', 'Bash(tail:*)', 'Bash(wc:*)',
    'Bash(grep:*)', 'Bash(rg:*)', 'Bash(sed -n:*)',
    'Bash(git status:*)', 'Bash(git diff:*)', 'Bash(git log:*)',
    'Bash(git show:*)', 'Bash(git grep:*)',
    'Bash(pytest:*)', 'Bash(.venv/bin/pytest:*)', 'Bash(uv run pytest:*)',
    'Bash(python -m pytest:*)', 'Bash(python3 -m pytest:*)',
    'Bash(.venv/bin/python -m pytest:*)',
    'Bash(lint-imports:*)', 'Bash(.venv/bin/lint-imports:*)',
    'Bash(uv run lint-imports:*)',
    'Bash(make test:*)', 'Bash(make lint:*)', 'Bash(make check:*)',
    'Bash(docker ps:*)', 'Bash(docker images:*)', 'Bash(docker inspect:*)',
    'Bash(docker logs:*)', 'Bash(docker version:*)', 'Bash(docker info:*)',
    'Bash(fleet --help:*)', 'Bash(fleet status:*)', 'Bash(fleet project ls:*)',
    'Bash(fleet work show:*)', 'Bash(fleet run show:*)',
    'Bash(fleet attention list:*)', 'Bash(fleet decision list:*)',
    'Bash(fleet decision record --help:*)', 'Bash(fleet attention add --help:*)',
)


def dispatch_rules(profile: str | None, rules: list[str] | None, *, agent: str) -> list[str]:
    if profile is None:
        return list(rules or [])
    if profile != 'review':
        raise FleetError(f"unknown allow profile {profile!r}; available: review")
    if agent != 'claude':
        raise FleetError('--allow-profile applies to Claude jobs only; Codex uses its sandbox')
    return list(dict.fromkeys([*REVIEW_RULES, *(rules or [])]))
