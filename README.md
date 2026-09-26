# agent-fleet

Send task lists to Claude Code and Codex agents on other machines, watch them
from here, and let a local Claude orchestrate them.

```
local: fleet CLI / fleet web ──ssh (shared connection)──▶ host: fleetd.py
                                                            └ tmux -L fleet: runner → claude -p / codex exec
                                                              ~/.fleet/jobs/<id>/{job.json,events.jsonl,context/,outbox/}
```

- **fleetd** (`fleet/remote/fleetd.py`, stdlib only) owns job state on each host. A
  runner in a private tmux server works through the job's steps in one agent
  session (`--resume` / `codex exec resume`), normalising the agent's JSON stream
  into `events.jsonl` and the agent's own todo list.
- **fleet** (local CLI) calls fleetd over ssh with a shared ControlMaster connection
  and aggregates every host.
- **fleet web** keeps one `fleetd stream` per host over ssh (pushes job changes as they
  happen, heartbeat every 5s, reconnects when a host goes quiet) and pushes state to
  the dashboard (`fleet/web/index.html`) over server-sent events at `/api/stream`.
- **skill/SKILL.md** teaches a local Claude to dispatch, wait and read results
  (symlinked into `~/.claude/skills/fleet`).

## Setup

Clone this repository, then run:

```bash
cd agent-fleet
uv tool install .
fleet host add worker --ssh worker  # use an SSH host you can already connect to; --local for this machine
fleet install worker                # copies fleetd, finds claude/codex on the login-shell PATH
fleet unlock worker                 # once per boot: passphrase into the host's fleet ssh-agent
```

Re-run `fleet install <host>` after changing `fleetd.py`.
For development, use `uv sync --locked` in the checkout and `uv run fleet ...`.

## Use

```bash
fleet send -H worker -p myrepo -d "Fix flaky invoice tests" -C ~/src/myrepo \
  -s "Find why tests/test_invoice.py is flaky" -s "Fix it and run the file" -c notes.md
fleet ls            # grouped by project: ✓ done  ▶ in flight  ○ upcoming
fleet watch         # live
fleet web --open    # dashboard; http://localhost:8787/?demo shows fake data
fleet wait worker:<id> && fleet result worker:<id>
```

## Dashboard access

`fleet web` binds to `127.0.0.1` by default. Its `/api/state`, `/api/stream`, and
`/api/doc` endpoints have no authentication and can show job descriptions,
activity, working directories, session details, and Markdown documents. Anyone who
can reach the dashboard can read that data. Keep it on loopback or use an SSH tunnel;
do not bind it to a shared network unless you have added access control in front of it.
Agents can place Markdown outside the job directory in the document list, so review
the dashboard's reachability before running jobs with sensitive files.

## Development checks

```bash
uv run --locked python -m unittest discover -s tests -v
uvx ruff@0.13.2 check fleet tests
uv build
```

The project code is licensed under Apache-2.0. Bundled assets have their own terms
in `fleet/web/assets/CREDITS.md` and `fleet/web/vendor/three/LICENSE`.

## Host prerequisites

python3 ≥ 3.8, tmux, rsync, and claude and/or codex logged in. The host's ssh-agent
for jobs is a user unit, `~/.config/systemd/user/fleet-ssh-agent.service`
(`ssh-agent -D -a %t/fleet-ssh-agent.sock`), with lingering enabled so it outlives logins.
