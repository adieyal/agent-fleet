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
- `-p` groups jobs by project in `fleet ls` and the web view — use the repo or
  initiative name, consistently.

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

A step that ends `FLEET_STATUS: blocked` holds its job: later steps wait until
it is answered. `fleet add host:id -s "reply"` on such a job answers that step
(it says so); the reply runs next, then the steps queued behind it. Use
`--no-answer` to just append. `fleet answer <attention-id> "reply"` on the
step's attention item does the same and resolves the item.

An agent in a job records its own decisions with `fleet decision record
--work-item W --question Q --answer A --principle P --actor A`. On the
controller it writes the store directly; on a worker host (whose store does not
hold the job's run) it prints `Decision <id> handed to the controller via job
<job>'s stream`, and `fleet web` records it once it hears from that host (an
unrecordable one, e.g. an unknown work item, becomes an alert). Check with
`fleet decision list --project P`.

The user watches the same jobs with `fleet watch` and `fleet web` (the
kitchen dashboard), so keep descriptions and step titles meaningful.
