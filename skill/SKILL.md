---
name: fleet
description: Dispatch tasks to Claude Code or Codex agents running on other machines (over ssh), give them context files, monitor progress, and get notified when they finish. Use when the user asks to send, delegate, farm out or run work on a remote machine/box/host, check on remote agents, or orchestrate several agents in parallel.
---

# Fleet: remote agents over ssh

`fleet` runs jobs on hosts listed in
`~/.config/fleet/config.json`. A **job** is one agent (claude or codex) in one
working directory on one host, working through an ordered list of **steps**.
Steps run one after another in the same agent session, so later steps see
earlier work. Job refs are `host:id` (a unique bare id also works).

Run `fleet hosts` first if you don't know which hosts exist.

## Dispatch

```bash
fleet send -H home -p <project> -d "<one line: what this job is doing>" -C <cwd on host> \
  -s "<step 1 prompt>" -s "<step 2 prompt>" \
  -c ./spec.md -c ./data/            # context: copied to the job's context dir
  [-a codex] [-m <model>] [--permission <mode>] [--json]
```

- Steps can come from `-f tasks.md` (one step per `-`/`1.` list item) or a JSON list.
- `--work-item W` links the whole job to stored work. When steps work through
  different items (say one milestone each), name each step's own:
  `-s "Do M1" --step-work-item <M1> -s "Do M2" --step-work-item <M2>` (the flag
  names the `-s` just before it), or `{"prompt": "…", "work_item": "<id>"}` in a
  JSON steps file. The deck then shows each item active while its step runs.
  Step items must be in the job's project. `fleet add` takes the same flags.
- Write each step as a self-contained instruction with a checkable outcome. The
  agent can't ask you questions; say what to do when blocked (stop and report).
- The agent is told its context dir and an **outbox** dir for files meant for you.
- Permissions: claude defaults to `acceptEdits` (Bash only if the host's
  settings allow it); codex defaults to `workspace-write`. Only use
  `bypassPermissions` / `danger-full-access` when the user asked for it.
- The store records who sent, answered or retried: `--actor NAME`, by default
  `job:$FLEET_JOB_ID` inside a fleet job and `user` otherwise. `send` prints the
  run, permission and guidance versions it started the agent with.
- `-p` selects a registered project (ID, prefix or name). Use `fleet project ls`
  to find it. Fleet derives the job's host label from its registered link; for
  example, `fleet project add "Agent Fleet" --link home:agent-fleet` lets
  `fleet send -H home -p "Agent Fleet" ...` use the worker label `agent-fleet`.
  Missing links are refused with the exact `fleet project link` command to run.
  Multiple labels on that host must be reduced to one before dispatch. Raw host
  labels are accepted only in explicit `HOST:LABEL` link commands, never as `-p`.
  Exact IDs take precedence, then exact names, then unique ID prefixes. Duplicate
  names or ambiguous prefixes are refused. The same references work in `ls`,
  `watch`, `mv`, work, attention, guidance, history and project maintenance.
- `--hold` creates the job without starting it; `fleet start host:id` starts it.

## Get notified of completion

After `send`, start a background wait so you are re-invoked when it ends:

```bash
fleet wait home:ab12cd --json        # run with run_in_background: true
```

It exits 0 when every step is done, 1 if any failed/cancelled, and prints each
step's final summary. Wait on several with `fleet wait a b c`, or `--any` to
return on the first. For a long-lived feed of every step/job change across all
hosts, run `fleet notify` under the Monitor tool.

## Inspect and steer

| Need | Command |
|---|---|
| Everything, grouped by project | `fleet ls` (`--by host`, `-p proj`, `-b` brief, `--json`) |
| One job: steps, todos, workspace (repo, worktree, branch, uncommitted), recent activity | `fleet show host:id` |
| Store-backed history, including sessions and unlinked runs | `fleet history runs --project PROJECT --since 7d` |
| Runs serving work or its descendants | `fleet history runs --work-item ID --descendants` |
| Archived steps, commits, pushes, documents and trace | `fleet run show RUN_ID_PREFIX [--json]` |
| Database rows and retained file sizes | `fleet store usage [--json]` |
| Full final message of each step + outbox listing | `fleet result host:id [--step N]` |
| Fetch files the agent left for you | `fleet pull host:id [dest]` |
| Send more context mid-job | `fleet push host:id file…` then mention it in the next step |
| Queue follow-up work (restarts an idle job) | `fleet add host:id -s "…"` (`--retry` re-queues failed steps) |
| Answer a blocked step | `fleet add host:id -s "reply"` (or `fleet answer <attention-id> "reply"`) |
| Stop | `fleet cancel host:id [--all-steps]` |

Read results with `fleet result` before reporting to the user or dispatching
dependent work; don't trust a `done` status alone. Status meanings: `running`,
`queued` (steps pending), `stalled` (runner died mid-step), `failed`, `done`,
`cancelled`, `blocked`.

History reads the controller store, so it works while workers are offline.
`history runs` also accepts `--host`, comma-separated `--status`, `--kind job|session`,
`--unlinked`, `--until DATE`, `--limit N`, and `--json`. Its default limit is 50;
read the `N of M` footer before claiming a complete list. History statuses are
`running`, `succeeded`, `failed`, `stopped`, and `unknown outcome`. Date filters
compare recorded start times; missing starts remain visible without a date filter.
Terminal jobs retain normalized events on the controller; raw traces remain on
the worker. `fleet rm` records worker trace removal and preserves retained evidence.

A step that ends `FLEET_STATUS: blocked` holds its job: later steps wait until
it is answered. `fleet add host:id -s "reply"` on such a job answers that step
(it says so); the reply runs next, then the steps queued behind it. Use
`--no-answer` to just append. `fleet answer <attention-id> "reply"` on the
step's attention item does the same and resolves the item.

An agent in a job records its own decisions with `fleet decision record
--work-item W --question Q --answer A --principle P --actor A`. On the
controller it writes the store directly; on a worker host (whose store does not
hold the job's run) it prints `Decision <id> handed to the controller via job
<job>'s stream`, and `fleet serve` records it once it hears from that host (an
unrecordable one, e.g. an unknown work item, becomes an alert). Check with
`fleet decision list --project P`.

To see who changed a work item, attention item or project and from which run,
run `fleet history --subject <id or prefix> [--since 7d]`. Changes made inside a
job name the job's run. Never prune history (`fleet history prune`) unless the
user asks.

The user watches the same jobs with `fleet watch` and `fleet web` (the
kitchen dashboard), so keep descriptions and step titles meaningful.

## Independent observer and deck

`fleet serve` owns host observation, retained traces and existing authorized
triage scheduling. Keep it running on the controller independently of the deck.
`fleet web` subscribes; it starts no followers/scheduler and shows “Runtime
unavailable — start fleet serve” when the observer is absent. Fixture/demo mode
needs no observer. `fleet serve status` prints owner PID, uptime, named worker
health and per-host stream state; an offline host is distinct from a dead worker.
A second owner for the same canonical store is refused before workers start.

For supervised controller installation, use the repository README's
“Controller deployment with systemd user services” section and
`scripts/systemd/fleet-serve.service` / `fleet-web.service`: install all four wheels,
stop the old web owner, then start serve, verify status and start web. Restarting
web alone leaves ingestion running. Installing units or changing a live controller
still needs authorization from the task; these instructions supply none.

## Attention and project triage

When raising user attention, include `--reason` explaining why the user must
act. For example, a destructive operation needs the user's decision:

```bash
fleet attention add "Approve deleting old artifacts" --project <project> --kind decision --owner user \
  --source agent --source-reference <unique-ref> \
  --context-reference "outbox/REPORT.md" --reason "Deleting these artifacts is irreversible" --actor codex
fleet attention delegate <item-id> --actor user --note "Let the project's triage agent inspect this failure"
fleet attention take <item-id> --actor user
fleet triage status <project>
```

Delegating changes ownership and keeps the item open and visible. Take back
revokes the agent's ability to act on it. Session questions require their
terminal and cannot be delegated. A confirmed project triage mandate is required
for automatic triage; merely writing a draft does not grant authority. Existing
items stay with the user. Inspect status for the pinned mandate version, queue,
live run, remaining daily budget and delivery error. See CONTEXT.md for retry,
timeout and untouched-run limits. Triage control commands record Decisions;
triage never completes work or judges criteria.

## Test suite

Run the non-browser suite on any host:

```sh
uv run pytest -q -m "not browser"
```

Browser tests are marked automatically from their Playwright fixture dependencies.
Run them on home with `uv run pytest -q -m browser`. On hosts without the
Playwright browsers they skip with the reason "Playwright browsers not installed
here; browser tests run on home". Browser tests also skip on other hosts even
if executables are installed. Tests use temporary Fleet paths; pytest rejects
access to the real `~/.config/fleet` store and config before opening them.

## Persisted work reads

`fleet work show ID_OR_PREFIX [--json]` reads work details, ancestors and linked records.
`fleet status PROJECT [--item ID_OR_PREFIX] [--depth N] [--open] [--json]` scopes the work tree; depth 0 shows only roots. Incomplete descendants of complete items remain visible with `--open`. Item scopes exclude unlinked attention.
`fleet attention list` includes open, acknowledged and snoozed items. Use `--all` when inspecting resolved history, or `--state resolved` for only resolved records.

## Checkout development

Fleet now uses four uv workspace distributions: library `fleet` under
`packages/fleet/src/fleet`, terminal `fleet_cli` under
`packages/fleet-cli/src/fleet_cli`, dashboard `fleet_web` under
`packages/fleet-web/src/fleet_web` (assets in `static/`), and stdlib worker
`fleet_worker` under `packages/fleet-worker/src/fleet_worker`. Tests stay at root.
Use `uv sync --locked`, `uv run fleet ...`, `uv run pytest -q -m "not browser"`
and `uv run lint-imports` from the checkout. Build with
`uv build --all-packages --wheel`; install the controller using
`uv tool install --force --reinstall --no-cache --find-links dist dist/fleet_cli-*.whl`.
The CLI loads the web subcommand from distribution metadata. After upgrading,
`fleet install HOST` copies the fleet-worker distribution's standalone `fleet_worker/fleetd.py` resource
and configures the worker; workers run the copied file on Python 3.8+ without installing workspace distributions.

Build all four wheels, including fleet-worker. Host calls check the wire protocol;
on a mismatch run `fleet install HOST`. `fleetd.py version` reports worker and
protocol versions for deployment tooling.

## Live pages

Write a live page when people need a persistent narrative with current work, attention or runs alongside it.
Pages live in the project's management repository. Use stable `block` IDs so comments keep their targets.
Directives occupy separate blocks with blank lines around them:

```markdown
::work{id=<work UUID> block=migration-work}

::attention{id=<attention UUID> block=scope-question}

::runs{project=p-1234abcd since=7d block=recent-runs}
```

```sh
fleet page write PROJECT SLUG --file page.md --actor codex --key unique-write-key
cat page.md | fleet page write PROJECT SLUG --actor codex
fleet page ls PROJECT
fleet page show PROJECT SLUG
fleet page show PROJECT SLUG --version 1 --json
```

Write prints the page URL path and confirmed Git revision. Reuse a key only for the same payload.
Version numbers start at 1; JSON resolves the selected revision's blocks against current Fleet records.
Worker jobs without the controller store must put the Markdown in their outbox and report it.

Page thread replies are messages, not decisions; they preserve the attention item's state and owner.
Use `fleet attention reply ID "text" --actor codex` for as many replies as needed. Messages include actor/time,
appear live on pages, and are included by `fleet attention list` and `fleet attention show ID`.
Close a thread explicitly with `fleet attention resolve ID --details "reason" --actor codex`;
re-open it with `fleet attention reopen ID --actor codex`. A reply to a resolved thread keeps it resolved.
`fleet answer ID "answer" --actor codex` still records a decision and resolves a decision item
(or answers a blocked step). The page offers this as the secondary **Answer & resolve** action.

## The canvas and its guidance

When a step's prompt is a canvas brief ("You are the builder for the work item …"), it lists guidance lines with their
sources, such as `[stage implement v2, line 4] use the warm image`. Follow them, and when one changes what you do, cite
its source in your summary. Rules in force outrank the decision scope: when the brief says to ask before a kind of
decision, end the step with `FLEET_STATUS: blocked — <your question>` rather than deciding.

With the controller's CLI, `fleet canvas can PROJECT KIND` answers whether you may decide (`decide`, `tell` or `ask`)
an `impl`, `deps`, `ops`, `destructive`, `interface`, `arch`, `scope` or `process` decision. `fleet canvas show
PROJECT` gives the board as text. `fleet canvas op PROJECT OP --args JSON` sends the same operations the canvas does,
recorded under your actor. Regions may refuse agents, or turn an agent's move into a proposal for the user.
