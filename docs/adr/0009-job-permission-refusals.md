# Answer a job's permission refusals by changing its permissions

**Status:** Accepted

## Context

A Fleet job runs Claude non-interactively (`claude -p`). When it asks for a permission its rules do not grant, the `PermissionRequest` hook fires and the request is refused on the spot; the agent carries on and the `PostToolUse` that would close the request never comes. Recording each request as its own attention item with a free-text answer box, as interactive sessions do, produced one open item per refused command (56 from one job) and no way to act on any of them: an answer delivered after the fact cannot approve a command that has already been refused.

## Decision

A job's refused permission requests are one attention item per job step (owner: the job; source reference: the job and step index), not one per request. Its headline names the project, the step, the count and the tools in at most 12 words, and later refusals in the same step update it. It lists every refused request with the Claude permission rules that would have allowed it. Interactive sessions keep one item per request, because a person is at their prompt.

The item is answered by changing the job's permissions, not in words. **Allow these for this job** adds the proposed rules, deduplicated; **Allow all Bash for this job** adds `Bash`; **Dismiss** changes nothing. Each resolves the item with details saying what was done. A free-text answer to it is refused.

A grant travels over the existing fleetd transport as `fleetd grant <job> --step N --key K --schema-version 1` with the rules as a JSON list on stdin. The worker adds them to the job's `allowed_tools`, the list it already passes to every later `claude` run as `--allowedTools`, and appends a step that resumes the job's session to continue the refused one. The worker applies a key once, so repeating a grant after a lost reply queues nothing more. A continuation step is used rather than `fleet add --retry` because the refused step usually ended `done` or `blocked`, which `--retry` does not re-run, and a resumed session keeps the step's context. The grant is synchronous: if the worker cannot be reached, the item stays open and the error is shown, rather than holding a pending intent as answer delivery does.

fleetd derives the rules because it knows the runtime's rule syntax: `Bash(<program> [subcommand]:*)` for each simple command in a compound one, `Read(//<absolute path>)` and the like for file tools, `WebFetch(domain:<host>)`, and the bare tool name otherwise. They reach the controller as an optional `rules` list in each observation's request. Execution owns the grant (input to runs, per [ADR 0008](0008-business-ownership-boundaries.md)); Attention owns the batch and its resolution.

When the step ends (done, failed or cancelled) with the item untouched, it resolves as `refused; step finished`; a job the worker no longer reports once it has sent a full pass resolves it as `refused; job finished or removed`. Per-request job items recorded before this change fold into their step's item when fleetd replays their observation, and any left over after the first full pass resolve as superseded.

## Interactive sessions

Interactive sessions get the same hooks from the host's Claude user settings: `fleet hooks install <host>` has fleetd merge `PermissionRequest`, `PreToolUse` (matcher `AskUserQuestion`) and `PostToolUse` entries into `~/.claude/settings.json`, each marked `# fleet-session-hook` so `fleet hooks uninstall <host>` removes only them. The installed command does nothing, and succeeds, once fleetd or its FLEET_HOME is gone, and ignores processes with `FLEET_JOB_ID` set, because a job's own `--settings` hook records its events. A session's `AskUserQuestion` becomes one attention item headed by the question's header and the start of the question; the reader shows the questions, their options, the host, working directory and project, and says it is answered in that terminal. Fleet cannot type into a terminal, so the item has no answer box and closes on `PostToolUse`.

## Consequences

- The inbox holds one item per troubled job step, and each can be settled in one click.
- Allowing widens the job's permissions for all its later steps. The reader shows the exact rules before the click, and a Bash prefix rule such as `Bash(python3:*)` can be broad.
- The stream protocol version is unchanged: the new `rules` field is optional both ways. An older fleetd sends no rules, so only **Allow all Bash** is offered for its jobs; it also lacks `grant`, so allowing fails with its error and the item stays open. An older web ignores the field and keeps its per-request items.
- Refusals that arrive after the item was resolved in the same step are not shown; if the continuation step is refused again, that step gets its own item.
