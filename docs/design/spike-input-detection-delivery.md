# FS-003: input detection and delivery spike

Run on carbon, 2026-09-27, with Claude Code 2.1.283 and codex-cli 0.157.1. The probes create temporary homes, copy only local authentication/state needed to start the CLIs, and pass Claude hook settings explicitly. They do not edit the user's Claude or Codex settings. Each command below is run from this repository. Session IDs and temporary paths vary.

## Detection

### Claude Code headless

Command: `python3 scripts/spikes/claude_input.py headless`

Observed excerpt:

```text
exit: 0
stream: ... ('system', 'permission_denied', '<session-id>') ... ('result', 'success', '<session-id>')
tools: [('Bash', {'command': 'touch marker.txt', ...})]
hooks: [('PermissionRequest', None, 'default'), ('Stop', None, 'default')]
```

The `PermissionRequest` hook fires for an unapproved write in the temporary working directory. The `Notification` hook did **not** fire in this run. `Stop` fires when the turn ends, including after permission denial. `claude -p` did not remain suspended for input: it returned a final response asking for approval after the tool was denied. A Stop event therefore cannot by itself mean “waiting for input.” The headless stream has a `permission_denied` system event with `--include-hook-events`, but fleetd currently does not pass that flag or parse this event. This is one observed permission attempt, not a reliability guarantee for every permission type or question.

### Claude Code interactive

Command: `python3 scripts/spikes/claude_input.py interactive`

Observed excerpt:

```text
tty_tail: ... Select login method: ... Claude account with subscription ...
waiting_at_capture: True
exit: 143
hooks: []
```

The isolated interactive TTY reached Claude's login/onboarding screen, before the prompt was accepted. The script terminated only its own process after 45 seconds. Thus this run did not establish whether `Notification`, `PermissionRequest`, or `Stop` fires at an actual interactive permission or input wait. Do not treat the absence of hook records here as a negative hook result. The transcript parser in fleetd can see an interactive Claude `AskUserQuestion` or `ExitPlanMode` tool call when one is written, but that is evidence of the tool call, not proof that the session is still waiting.

### Codex exec

Commands: `python3 scripts/spikes/codex_input.py exec` and `python3 scripts/spikes/codex_input.py question`

Observed excerpts:

```text
first_events: [('thread.started', None), ('turn.started', None), ('item.completed', 'agent_message'), ('turn.completed', None)]
first_text: ['CODEX_FIRST']
notify: ['agent-turn-complete']

first_events: [('thread.started', None), ('turn.started', None), ('item.completed', 'agent_message'), ('turn.completed', None)]
first_text: ['Which do you choose: A or B?']
notify: ['agent-turn-complete']
```

`notify` fired for both a routine completed turn and a question that needs a human reply. The `exec --json` event types were also identical. In this question run Codex exited normally; no waiting event or suspended process was observed. A controller can classify a question from the actual message text only with uncertain interpretation, so neither `notify` nor these JSON event types are a reliable waiting-for-input signal.

### Codex interactive

Command: `python3 scripts/spikes/codex_input.py interactive`

Observed excerpt:

```text
tty_tail: ... \x1b[6n ...
waiting_at_capture: True
exit: -15
notify: []
```

The isolated pseudo-terminal did not answer the TUI's terminal queries, so the probe never reached an agent turn. This run does not establish whether Codex interactive `notify` signals a real wait. fleetd discovers interactive Codex sessions from rollout transcripts, but its `CodexRolloutParser` handles completed messages, commands, plans and errors, not a waiting event.

## Delivery

### Claude Code headless

Command: `python3 scripts/spikes/claude_input.py resume`

Observed excerpt:

```text
first_exit: 0 session_id: <session-id>
exit: 0
result: ['RESUMED']
hooks: [('Stop', None, 'default'), ('Stop', None, 'default')]
```

Use `claude -p <answer> --resume <session-id>` as a new process after the previous headless turn exits. The probe confirmed that the answer prompt reached the same session ID. This is a new turn, not delivery into a blocked permission dialog. A fleetd delivery command must associate the accepted answer with a stable delivery key before starting a resumed process; resume alone does not provide exactly-once delivery after a lost response.

### Codex exec

Command: `python3 scripts/spikes/codex_input.py resume`

Observed excerpt:

```text
thread_id: <thread-id>
resume_exit: 0
resume_events: ['thread.started', 'turn.started', 'item.completed', 'turn.completed']
resume_text: ['CODEX_RESUMED']
```

Use `codex exec resume --json <thread-id> <answer>` as a new process after the previous turn exits. The probe confirmed answer delivery to the same thread. As with Claude, fleetd must own the delivery key and reconcile an uncertain process result; the CLI resume command is not an exactly-once transport.

### Claude Code interactive

Command: `python3 scripts/spikes/claude_input.py interactive` produced the login-screen output above. No answer was delivered. fleetd does not own the terminal for independently launched interactive sessions, so it cannot safely write an answer there today. The discovered session's `resume` string is `claude --resume <id>`; that is for continuing a stopped session, not injecting text into a live prompt. Delivery for these sessions remains manual until Fleet explicitly owns their terminal or a runtime-supported input channel is verified.

### Codex interactive

Command: `python3 scripts/spikes/codex_input.py interactive` produced the terminal-query output above. No answer was delivered. fleetd likewise does not own these terminals. Its displayed `resume` string is `codex resume <id>`, which continues a session through a user terminal; it does not send text into an independently running TUI. Delivery remains manual until a controlled terminal or supported input API is demonstrated.

## What fleetd sees now

`fleet/remote/fleetd.py` runs each job runner in a private `tmux -L fleet` session (`launch_runner`, lines 488–498), but each agent step is a separate `Popen` with `stdin=DEVNULL` and JSON stdout (`run_step`, lines 385–433). The tmux pane hosts the runner and log pipeline; writing to that pane is **not** writing to Claude or Codex. `agent_command` already uses `--resume` for subsequent Claude steps and `exec resume` for subsequent Codex steps (lines 333–361).

Interactive sessions are discovered by scanning recent Claude project and Codex rollout transcripts (`SessionTracker`, lines 875–943). They are not fleetd-owned processes. “idle” means 90 seconds without transcript writes, which also happens during a long tool call. `fleet/attention.py` currently raises a session decision only when its latest parsed tool activity is `AskUserQuestion` or `ExitPlanMode` (`WAITING_TOOLS`, lines 30 and 52–61). That misses ordinary text questions, headless permission denials, and Codex questions. A tool call can also become stale if its result is not visible yet.

## Observation shape

Emit one sourced observation per confirmed transition, with `host`, `runtime`, `owner_type` (`job` or `session`), `job_id` when applicable, `session_id`, `step_index` when applicable, `kind` (`input_requested`, `input_cleared`, or `turn_ended`), `reason` (`permission`, `question`, `plan_approval`, or unknown), `source_event` (hook name or stream/transcript record type), `source_event_id` or a stable occurrence timestamp, `observed_at`, and a context reference to the retained raw record. Use `(host, job_id)` for a job's source reference and `(host, session_id)` for an interactive session's; include the occurrence ID separately so repeat signals for the same request deduplicate while a later request can open a new attention item. Only emit `input_requested` from a source that actually identifies a request. Record absent/ambiguous signals as unknown, and do not resolve a request on host silence.

## Cannot detect or deliver yet

The probes did not prove reliable interactive hook behavior, a Codex waiting event, or any way to inject an answer into independently owned live terminals. Headless permission requests are denied rather than held open in the tested `claude -p` mode; answering requires a new turn that may retry the denied operation. Ordinary text questions have no machine-readable request identity in the observed Codex event stream. A Stop hook or `agent-turn-complete` cannot distinguish an input request from normal completion. Do not promise automatic interactive answer delivery or exactly-once effects of a resumed prompt from this evidence alone.
