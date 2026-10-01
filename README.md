# Agent Fleet

Run Claude Code and Codex jobs on your own machines. See what every agent is doing
from one terminal or a local dashboard.

Fleet sends an ordered task list to a host over SSH. The agent works through the
steps in one session, so later steps can build on earlier work. You can send context,
follow activity, add steps, and collect the result without juggling SSH tabs.

![Fleet deck with project rooms, agents, and live activity](docs/images/fleet-rooms.png)

*The deck groups jobs by project. Each android shows the host and agent, while the
log and speech bubbles show current work. Screenshot uses synthetic demo data.*

## What you can do

- Send steps to Claude Code or Codex on an SSH host or this machine. Attach files
  or directories as context and choose the agent's permission mode.
- Use `fleet ls`, `fleet watch`, or the deck to see progress, failures, todos,
  and active interactive Claude Code or Codex sessions across hosts.
- Read step reports, open Markdown documents in the deck, and pull files the
  agent placed in its outbox.

Fleet uses your SSH access and a small Python host runner. There is no central
service to deploy.

## Get started

You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer locally. On each
host, you need SSH access, Python 3.8 or newer, `tmux`, `rsync`, and an authenticated
Claude Code or Codex CLI.

```bash
git clone https://github.com/adieyal/agent-fleet.git
cd agent-fleet
uv tool install .

fleet host add worker --ssh worker  # any SSH target you can already reach
fleet install worker                # copy the runner and locate agent CLIs
```

Use `fleet host add laptop --local` for this machine. **After upgrading, reinstall
fleetd on every host with `fleet install <host>`: the wire protocol changed.**

Send a job with a clear goal and checkable steps:

```bash
fleet project add myrepo --link worker:myrepo
fleet send -H worker -p myrepo -d "Fix flaky invoice tests" -C ~/src/myrepo \
  -s "Find why tests/test_invoice.py is flaky" \
  -s "Fix it and run that test file" \
  -c notes.md
```

Check on it from the terminal or open the deck:

```bash
fleet ls
fleet web --open
```

Use `fleet watch` for a live terminal view. When the job finishes, run
`fleet result worker:<id>` for its reports or `fleet pull worker:<id> ./results`
for outbox files. `fleet wait worker:<id>` blocks until it finishes.

After starting the deck, open `http://localhost:8787/?demo` to explore it with
synthetic jobs. The demo works even when the configured host is offline.

Room signs can use friendly names without changing the project identifiers used by
jobs. Add `"project_labels": {"restoke-analytics": "Bang bang!"}` to
`~/.config/fleet/config.json` (or the file selected by `FLEET_CONFIG`), then
restart `fleet web`.

Projects have stable IDs in the persistent store. Host labels are linked explicitly;
matching repository URLs only suggest links in `fleet project ls`. The deck's
`/api/state` reports a null `project_id` when a job or session's label is unlinked.

Focus is where you are putting resources: priority or background. Each room in the
deck has a priority/background switch at its front corner; one click sets it. A
background room is dimmed and desaturated, its androids lose their speech bubbles and
stay nearly still however busy they are, and its props rest. Rooms never move when
focus changes. Work is focused through its registered project when its label is
linked, otherwise through the label itself, so unregistered rooms can be focused too.
Anything you have never set is in priority. Choices are live workspace state in
the SQLite store, and every open
deck updates as soon as one changes.

Attention items are stored questions, blockers and alerts, raised by host observations,
work, proposals or manual commands. They survive restarts. Lanterns show outstanding
items on rooms and floors; the front desk includes items from shuttered projects.
Select an item to open its context and answer in the reader panel. Reading never
acknowledges it. Acknowledging keeps it visible; snoozing hides it until its deadline.
A quiet host leaves its items intact with last-seen information. Source observations
can resolve items when the underlying condition clears; you can also resolve them
explicitly. `/api/state` lists them under `attention`.

The header's **deck | building** switch shows the same fleet as a building seen in
cross-section, one floor per registered project; the deck stays the default and the
choice is remembered. Priority floors are open, background floors are windowed and
glow warm while runs are active, and free floors say "To let". The building has six
floors unless you set another number with `fleet building capacity N` (1 to 10);
the deck never offers to raise it. A project keeps its floor in the store: the lowest free one when it
moves in. The lobby shows the host key and any visitors, labels with work that no
project claims; **Move in** registers one as a project on the lowest free floor.
A floor with attention items gets one lantern beside it (items on no floor hang theirs
by the lobby). Each name plate has an open · windows switch for the project's focus.
Click a floor to enter it: the deck shows just that project's work, with a lift panel
on the right edge (a button per floor, L for the whole building); Esc steps back out.

Pull a floor's shutter handle to shutter its project: it goes to the storehouse beside
the lobby as a crate, its floor says "To let", and you have a few seconds to undo. It
keeps its ID, links and focus; runs already going finish and show on its crate; its
attention moves to the front desk and the storehouse door. Open a crate to look around
the project read-only, or restore it to its old floor if that is free (the lowest free
floor otherwise). When every floor is taken, moving in or restoring asks which floor to
clear, or you can cancel. A shuttered project starts no new work; from the CLI,
`fleet project restore PROJECT_ID [--shutter OTHER_ID]` brings it back.

## Getting started with the workspace

The controller keeps projects, floors, focus, work, attention and execution state in
`fleet.db` beside `~/.config/fleet/config.json` (or beside `FLEET_CONFIG`). Set
`FLEET_STORE` to override the database path. On first use, Fleet imports legacy
projects and workspace state from `config.json` and `workspace.json` once, keeping
backups and the original files. Hosts and display settings still use `config.json`.

An explicit `FLEET_CONFIG` must name an existing file; every command fails if it
is missing. An explicit `FLEET_STORE` must also exist, except that `fleet web`
initializes a missing store and announces its path. Without these overrides,
first use still creates the store at the default location.

The examples below use a local host. Replace `PROJECT_ID`, `DUPLICATE_ID`, `WORK_ID`,
`CRITERION_ID`, `ATTENTION_ID` and `RUN_ID` with IDs printed by preceding commands.
Use an existing host directory for `WORKING_DIRECTORY`. `JOB_ID` means an existing job on that host.
To experiment independently, set `FLEET_CONFIG`, `FLEET_STORE`, `FLEET_HOME` and
`FLEET_MANAGEMENT` to paths in a temporary directory before starting. Create the config file first
(for example, `{"hosts": {"workspace-demo": {"ssh": null}}}`), then run
`fleet web` once to initialize the store before using the commands below.

```bash
fleet host add workspace-demo --local
fleet project add "Workspace demo" --link workspace-demo:demo
fleet project ls --no-suggest
```

The project ID stays stable when renamed. Per-host duplicates can be folded into
the older project, preserving its ID and collecting the other's links. For example:

```bash
fleet project add "Duplicate demo"
fleet project merge PROJECT_ID DUPLICATE_ID
```

Work items carry goals, conditions and next steps. Criteria are checked against
recorded evidence, judged within a mandate, or accepted by the user. A successful
run never completes work automatically; progress without a known total is unknown.

```bash
fleet work add "Write the guide" --project PROJECT_ID --kind milestone --goal "Ship the guide" --actor user
fleet work set WORK_ID --next-step "Review the guide" --actor user
fleet criterion add WORK_ID "Guide reviewed" --verification accepted --actor user
fleet criterion meet CRITERION_ID --actor user
```

A project's management repository is created for it on its first record, under
`~/.local/share/fleet/management/PROJECT_ID` (`FLEET_MANAGEMENT` moves that home), so
nobody has to choose a path. To keep it somewhere else, register an existing Git
repository before the first record with `fleet project management PROJECT_ID PATH`;
registration is permanent and migrates legacy summaries once. Summaries, mandates and guidance are committed there, with paths and
confirmed revisions in the store. Structured decisions remain store-owned.

```bash
fleet summary set WORK_ID --purpose "Ship the guide" --done "Draft written" --doing "Review" --next "Publish" --authoring-role user --actor user
fleet status PROJECT_ID
```

A project's constitution and each epic's charter are Markdown in the management
repository; every edit is a new version committed with its actor. Give an epic ID
instead of a project to work on its charter, which shows the constitution version
it inherits. Without `--file`, `edit` reads stdin; `show --version N` prints an
older version from `history`. A job sent, dispatched or orchestrated on a work item
gets the constitution and its nearest epic's charter as `CONSTITUTION.md` and
`CHARTER.md` in its context directory, and a short paragraph on applying them;
`fleet status` shows the versions each run received.

```bash
fleet guidance edit PROJECT_ID --file CONSTITUTION_PATH --actor user
fleet guidance show PROJECT_ID
fleet guidance history PROJECT_ID
```

An agent records what it decided itself, naming the principle it relied on. Inside
a fleet job the decision is linked to the job's run and the guidance versions it
received; elsewhere give `--run` or leave the versions unknown. When the job's
run is held by another machine's store (a job on `home` dispatched from the
controller on `carbon`), the command hands the decision to the host's fleetd and
prints its id: the controller records it from the job's stream when `fleet web`
next hears from that host, and raises an alert if it cannot. List decisions for
a project or an epic (`--epic EPIC_ID`), newest first.

```bash
fleet decision record --work-item WORK_ID --question "Rerun the flaky test?" --answer "Once" --principle "Constitution: decide yourself — test-only fixes" --actor claude
fleet decision list --project PROJECT_ID
```

In the deck, the floor opens the constitution and each epic's page shows its
charter and the decisions on its work. Edit either in place (each save is a new
version by `web-user`, refused if someone saved first), open older versions in
the reader, and promote a decision into the charter's decisions in force, or
from the shell with `fleet guidance promote DECISION_ID --epic EPIC_ID --actor user`.

Use the front desk and lanterns, or these commands, to manage attention:

```bash
fleet attention add "Review the guide" --project PROJECT_ID --work-item WORK_ID --kind decision --owner user --source manual --source-reference guide-review --context-reference README.md --actor user
fleet attention list --project PROJECT_ID
fleet attention ack ATTENTION_ID --actor user
fleet attention snooze ATTENTION_ID --until 2099-01-01T09:00:00+00:00 --actor user
fleet attention resolve ATTENTION_ID --details "Review handled separately" --actor user
fleet attention add "May we publish?" --project PROJECT_ID --work-item WORK_ID --kind decision --owner user --source manual --source-reference publish --context-reference README.md --actor user
fleet answer ATTENTION_ID "Yes, publish the guide" --next-step "Publish"
```

Use the second attention ID for the answer. Answering in the CLI or reader records
a decision and resolves the item. For a blocked job step, `fleet answer` instead
adds the reply as the job's next step on its host and resolves the item, as
`fleet add host:id -s "reply"` does. Live-session answers have separately tracked
delivery, so an offline host does not lose the answer.

Link existing jobs without fetching them, and add external references to the library
(a link grants no access). Run reports and traces also appear in the library; pruned
traces remain listed as unavailable.

```bash
fleet run link workspace-demo JOB_ID WORK_ID
fleet library link https://example.com/guide --work-item WORK_ID --title "Guide reference"
```

An unobserved run has unknown outcome, not failure. Only after investigating an
unknown outcome, explicitly close it to permit retry:

```bash
fleet run resolve-unknown RUN_ID
```

These commands start real agents on your configured host. Choose its runtime,
working directory and permission deliberately. `send --project` is the host label
(`demo` here), while `--work-item` links the run to persistent work.

```bash
fleet dispatch WORK_ID "Review the guide" --host workspace-demo --runtime codex --cwd WORKING_DIRECTORY --permission workspace-write
fleet send --host workspace-demo --project demo --work-item WORK_ID --description "Review guide" --agent codex --cwd WORKING_DIRECTORY --permission workspace-write --step "Review the guide"
```

For orchestration, first record a complete mandate. From this checkout's
`uv run --frozen python` interpreter, run the following, substituting your project
ID. `write_mandate` validates and commits it; use a new key when changing the body.

```python
import json
from fleet.composition import open_records

mandate = {
    "goal": "Review the guide and report the next step",
    "constraints": ["Do not publish or deploy"],
    "decision_authority": ["update_progress", "raise_attention", "dispatch", "summary", "record_decision"],
    "escalation_conditions": ["Ask the user before publishing"],
    "criteria_it_may_judge": []
}
result = open_records().write_mandate(
    "PROJECT_ID", "mandate.json", json.dumps(mandate), key="guide-mandate-v1", actor="user"
)
assert result["state"] == "confirmed", result
```

The valid `decision_authority` names are those above plus `accept`, which this
example intentionally withholds. To delegate judgement, list existing **judged
criterion IDs from this work item** in `criteria_it_may_judge`. Start explicitly:

```bash
fleet orchestrate WORK_ID --mandate mandate.json --host workspace-demo --runtime codex --cwd WORKING_DIRECTORY --permission workspace-write
```

The orchestrator must run on the controller's local host, with runtime permission
to write the controller store and management repository. Each activation pins a
mandate revision. Out-of-authority commands are rejected; an explicit proposal
raises attention for the user. There is no automatic scheduling. See
[management records](docs/design/records-authoring.md),
[activation commands](docs/design/authority-commands.md), the [design](docs/design/)
and [ADRs](docs/adr/) for the detailed contracts.

## Read the work

The deck collects step reports, Markdown files the agent wrote, and Markdown in its
outbox. Select a document to read it with a contents list, tables, footnotes, and
task lists. You can copy the Markdown source or download it as a `.md` file.

To browse documents from a local project repository, add it to the deck's library:

```bash
fleet library add agent-fleet ~/src/agent-fleet
fleet web
```

Open **Library** in the deck header to search its top-level Markdown files and
`docs/` tree. These files stay in their repository; the library reads them from the
machine running `fleet web`. Run `fleet libraries` to see configured roots.

Automatic discovery is the default. To choose the display order or hide documents,
add `.fleet/library.json` to the project repository:

```json
{
  "order": ["docs/design/philosophy.md", "docs/design/workspace-hierarchy.md"],
  "hide": ["README.md"],
  "show_unlisted": true
}
```

Paths are relative to the project root. Listed documents appear first, followed by
other discovered Markdown files. Set `show_unlisted` to `false` to show only the
listed documents; `hide` always takes precedence. The manifest controls the list,
not access to files through the local dashboard.

![Fleet Markdown reader showing a report, contents list, and table](docs/images/fleet-markdown-reader.png)

*The reader opens a report from the synthetic demo. The same view works for files
from real jobs.*

## How it works

```text
fleet CLI / fleet web ── SSH ──▶ fleetd.py on each host
                                  └─ tmux runner ─▶ claude -p / codex exec
                                     ~/.fleet/jobs/<id>/
                                       job.json, events.jsonl, context/, outbox/
```

`fleetd.py` uses the Python standard library. Fleet copies it to each host and runs
it over SSH. Jobs run in a private tmux server; subsequent steps resume the same
agent session. The local dashboard receives host updates over SSH and sends them to
the browser with server-sent events. The optional [Fleet skill](skill/SKILL.md) lets
a local Claude dispatch and monitor jobs.

Claude jobs default to `acceptEdits`; Codex jobs default to `workspace-write`. Use
`fleet send --permission` when a job needs a different mode.

### Dashboard access

`fleet web` binds to `127.0.0.1` by default. Its `/api/state`, `/api/stream`, and
`/api/doc` endpoints have no authentication and can show job descriptions, activity,
working directories, session details, and Markdown documents. The library endpoints
also expose Markdown under configured local project roots. Anyone who can reach
the dashboard can read that data. Keep it on loopback or use an SSH tunnel. Add
access control before binding it to a shared network. Its writes, `POST /api/focus`,
`POST /api/attention/…`, `POST /api/move-in`, `POST /api/shutter` and `POST /api/restore`,
change the persistent store; they refuse requests from pages on
other origins, but anyone who can reach the dashboard directly can use them.

Agents can place Markdown outside the job directory in the document list, so review
the dashboard's reachability before running jobs with sensitive files.

For jobs that need private Git access, the host can use a user-level SSH agent. One
setup runs `ssh-agent -D -a %t/fleet-ssh-agent.sock` as
`~/.config/systemd/user/fleet-ssh-agent.service` with lingering enabled. Run
`fleet unlock worker` once per boot to add the key to that agent.

## Development

To try a branch alongside the installed fleetd, set `FLEET_FLEETD_PATH` to a
separate worker script path (for example `~/.local/share/fleet-branch/fleetd.py`)
and `FLEET_REMOTE_HOME` to a separate worker state directory (for example
`~/.fleet-branch`). Copy the branch's `fleet/remote/fleetd.py` to that script path
on each target host yourself: `fleet install` updates the normal installation.
For a local host, `FLEET_FLEETD_PATH` can point directly into the checkout.
Both variables are controller-side overrides applied to worker invocations; also
use temporary `FLEET_CONFIG`/`FLEET_STORE` paths to isolate controller state.

For a review of the intended workspace model, start with the [domain description](CONTEXT.md)
and its [working design](docs/design/workspace-hierarchy.md). These describe planned
behaviour as well as concepts already present in Fleet. The proposed
[execution decision](docs/adr/0007-execution-control.md) and
[first working workspace plan](docs/design/first-working-workspace-plan.md) describe
what to build next and what can wait.

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uvx ruff@0.13.2 check fleet tests
uv build
```

The project code is licensed under [Apache-2.0](LICENSE). Bundled assets have
their own terms in [asset credits](fleet/web/assets/CREDITS.md) and the
[three.js license](fleet/web/vendor/three/LICENSE).
