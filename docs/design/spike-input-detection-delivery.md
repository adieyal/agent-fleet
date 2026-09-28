# FS-003: input detection and delivery spike

## FS-006 implementation

Claude jobs now receive explicit `--settings` hooks for `PermissionRequest` and
`PostToolUse`. For independently launched interactive sessions, opt in before
starting Claude with `claude --settings "$(python3 /path/to/fleetd.py input-hook-settings
--project PROJECT)"` (put the command on one line). This does not edit user settings.
Discovered sessions without these hooks cannot supply confirmed input transitions.

The hook command retains raw request and resume records atomically beneath
`FLEET_HOME/input-observations`, with a lock per job/session/step. The version 1
`input_observation` stream message carries the fields specified below, plus the
project label. PermissionRequest creates an occurrence; repeated requests with the
same tool name and input while outstanding reuse it. PostToolUse with matching
session, step, tool name and input clears it. Notification and Stop do not clear it.
The correlation is tool/input based because the spike did not establish a common
request ID in both hooks. This assumes one outstanding identical tool request per
session/step. Retained occurrences replay when the stream reconnects, including
cleared occurrences, so a missed clear still resolves the stored item.

Attention owns the accepted item through its facade; the web stream adapter uses
the configured host alias and resolves the project label through the registry.
The source is `runtime-input:<host>`; the source reference contains owner type,
host, job/session ID and occurrence ID. A new occurrence gets a new item. Replays
cannot reopen resolved occurrences or refresh their last-seen time. A matched
clear records exactly `answered in session`. Silence, removal, Stop and ordinary
heartbeats do not resolve hook-sourced items. Raw records remain worker-owned.

Headless permission denial is a request needing attention, **not a suspended job**.
It remains open unless matching tool execution is later observed or the user
resolves it manually. This change adds no automatic delivery. Codex remains
undetectable from the tested notify/turn events: no automatic input observation
is generated; use `fleet attention add` for Codex questions. Existing legacy
session activity attention is unchanged.

At the checkpoint run `bash scripts/checks/waiting-for-input.sh PROJECT CARBON_CWD`
with this fleetd installed on carbon and the web ingester running. It starts a
real Claude job and describes the separate interactive approval check. The gates
only check the script syntax; they never start a paid runtime or contact carbon.

Run on home, 2026-09-27, with Claude Code 2.1.281 and codex-cli 0.154.0 (`claude --version`; `codex --version`). These results supersede the initial carbon probes (Claude 2.1.283 / Codex 0.157.1), which verified headless behavior but stopped at onboarding/terminal queries interactively. The probes create temporary homes, copy local authentication/state needed to start the CLIs, and pass Claude hook settings explicitly. They do not edit the user's Claude or Codex settings. Each command below is run from this repository. Session IDs and temporary paths vary; excerpts omit terminal escape sequences and repetitive stream records.

The interactive probes own a 120×40 pseudo-terminal, answer terminal queries, acquire a controlling terminal, and accept trust only for their own temporary directory. Claude's copied onboarding state must live at `$CLAUDE_CONFIG_DIR/.claude.json`. Small input pacing delays let the TUIs finish handling the preceding key. Both probes stop their own processes and delete their temporary homes on completion. Codex prints a warning that helper aliases cannot be created under `/tmp`; this did not prevent these no-tool turns.

## Detection

### Claude Code headless

Command: `python3 scripts/spikes/claude_input.py headless`

Observed excerpt:

```text
exit: 0
stream: ... ('system', 'permission_denied', '<session-id>') ... ('result', 'success', '<session-id>')
tools: [('Bash', {'command': 'touch /tmp/fleet-claude-spike-.../marker.txt', ...})]
hooks: [('PermissionRequest', None, 'default'), ('Stop', None, 'default')]
```

The `PermissionRequest` hook fires for an unapproved write in the temporary working directory. The `Notification` hook did **not** fire in this run. `Stop` fires when the turn ends, including after permission denial. `claude -p` did not remain suspended for input: it returned a final response asking for approval after the tool was denied. A Stop event therefore cannot by itself mean “waiting for input.” The headless stream has a `permission_denied` system event with `--include-hook-events`, but fleetd currently does not pass that flag or parse this event. This is one observed permission attempt, not a reliability guarantee for every permission type or question.

### Claude Code interactive

Command: `python3 scripts/spikes/claude_input.py interactive`

Observed excerpt:

```text
before_answer_hooks: ... "hook_event_name": "PermissionRequest", "tool_name": "Bash" ...
... "hook_event_name": "Notification", "message": "Claude needs your permission", "notification_type": "permission_prompt" ...
waiting_at_capture: True
answer_sent: True marker_exists: True
exit: 143
hooks: [('PermissionRequest', None, 'default'), ('Notification', 'permission_prompt', None), ('Stop', None, 'default')]
```

At a real permission dialog, `PermissionRequest` fires first. Leaving the dialog unanswered for seven seconds also captured `Notification(permission_prompt)`. An earlier run answered immediately and captured only `PermissionRequest` and `Stop`: Notification is delayed and cannot reliably describe short waits. `Stop` appeared after approval and completion, not while blocked. The process remained alive at capture because an interactive session returns to its prompt after a turn. Exit 143 is the probe's termination, not an agent failure. This establishes the tested Bash permission path, not every kind of question or idle notification. The transcript parser in fleetd can see `AskUserQuestion` or `ExitPlanMode` when written, but a tool call alone does not prove the session is still waiting.

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
before_answer_notify: [{'type': 'agent-turn-complete', ... 'client': 'codex-tui', ... 'last-assistant-message': 'Which do you choose: A or B?'}]
tty_tail: ... CODEX_ANSWER_RECEIVED ...
waiting_at_capture: True
exit: 0
notify: ['agent-turn-complete', 'agent-turn-complete', 'agent-turn-complete']
notify_messages: ['Which do you choose: A or B?', '{"title":"Choose A or B"}', 'CODEX_ANSWER_RECEIVED']
```

The TUI asked a real question and stayed at its prompt. The question, background title generation, and answer acknowledgement all emitted `agent-turn-complete`. Neither event type nor event count proves waiting or answer delivery. The probe checks the exact acknowledgement text before finishing. fleetd discovers interactive Codex sessions from rollout transcripts, but its `CodexRolloutParser` handles completed messages, commands, plans and errors, not a waiting event. A Codex permission dialog was not exercised here. [Official OpenAI notification documentation](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications) distinguishes the external `notify` program (turn completion) from TUI terminal notifications, which can include approval requests; this spike does not establish a fleetd collector for the latter.

A later run through `python3 scripts/checks/input_detection_delivery.py` reached its 60-second bound with `tty_tail: ... model: loading ...`, `notify: []`, and `notify_messages: []`. That repeat is inconclusive, not a negative notification result or evidence of successful delivery. The successful direct command above is the delivery evidence; the checkpoint runner prints observations for review rather than asserting that every runtime reached a turn.

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
resume_thread_ids: ['<thread-id>']
resume_text: ['CODEX_RESUMED']
```

Use `codex exec resume --json <thread-id> <answer>` as a new process after the previous turn exits. The probe confirmed answer delivery to the same thread. As with Claude, fleetd must own the delivery key and reconcile an uncertain process result; the CLI resume command is not an exactly-once transport.

### Claude Code interactive

Command: `python3 scripts/spikes/claude_input.py interactive` produced `answer_sent: True marker_exists: True` and the hook sequence above. The probe wrote Enter to its owned PTY at the approval dialog; the requested temporary file was then created and `Stop` fired. Recommend terminal input for a live interactive permission dialog **only when Fleet owns that terminal and can correlate its current request**. fleetd does not own independently launched sessions' terminals, so those still require manual delivery. Its displayed `claude --resume <id>` is for continuing a stopped session, not approving a live dialog. This probe verified permission approval, not arbitrary text or option selection.

### Codex interactive

Command: `python3 scripts/spikes/codex_input.py interactive` produced the question and `CODEX_ANSWER_RECEIVED` acknowledgement above. The probe wrote `A. Reply with exactly CODEX_ANSWER_RECEIVED.` and then Enter into the same live PTY. Recommend terminal input for a live interactive session that Fleet owns; retain manual delivery for sessions fleetd merely discovers. Its displayed `codex resume <id>` continues a session through a user terminal; it does not inject into an independently running TUI. This probe verified a text answer, not permission-menu selection. The terminal transport alone provides no request identity, acknowledgement protocol, or exactly-once guarantee.

## What fleetd sees now

`fleet/remote/fleetd.py` runs each job runner in a private `tmux -L fleet` session (`launch_runner`, lines 488–498), but each agent step is a separate `Popen` with `stdin=DEVNULL` and JSON stdout (`run_step`, lines 385–433). The tmux pane hosts the runner and log pipeline; writing to that pane is **not** writing to Claude or Codex. `agent_command` already uses `--resume` for subsequent Claude steps and `exec resume` for subsequent Codex steps (lines 333–361).

Interactive sessions are discovered by scanning recent Claude project and Codex rollout transcripts (`SessionTracker`, lines 875–943). They are not fleetd-owned processes. “idle” means 90 seconds without transcript writes, which also happens during a long tool call. `fleet/attention.py` currently raises a session decision only when its latest parsed tool activity is `AskUserQuestion` or `ExitPlanMode` (`WAITING_TOOLS`, lines 30 and 52–61). That misses ordinary text questions, headless permission denials, and Codex questions. A tool call can also become stale if its result is not visible yet.

## Observation shape

Emit one sourced observation per confirmed transition, with a wire `schema_version`, `host`, `runtime`, `owner_type` (`job` or `session`), `job_id` when applicable, `session_id`, `step_index` when applicable, `kind` (`input_requested`, `input_cleared`, or `turn_ended`), `reason` (`permission`, `question`, `plan_approval`, or unknown), `source_event` (hook name or stream/transcript record type), `source_event_id` or a stable occurrence timestamp, `observed_at`, and a context reference to the retained raw record. Use `(host, job_id)` for a job's source reference and `(host, session_id)` for an interactive session's; include the occurrence ID separately so repeat signals for the same request deduplicate while a later request can open a new attention item. Only emit `input_requested` from a source that actually identifies a request. Record absent/ambiguous signals as unknown, and do not resolve a request on host silence.

Prefer Claude's `PermissionRequest` with its tool input as early evidence, correlating a later `permission_prompt` notification to that request rather than creating another item. A headless denied request is not a suspended process: retain that distinction in context and delivery capability. Emit `input_cleared` only on correlated answer/tool-result evidence; a generic Stop/turn-complete is `turn_ended`, not proof an outstanding request was answered. Observations owns sourced records, Attention deduplicates requests, Decisions records accepted answers, and Execution owns delivery and its idempotency key. No production protocol is changed by this spike.

## Cannot detect or deliver yet

The probes establish Claude's tested interactive Bash permission signal and delivery into both probe-owned terminals. They do not establish signals for every interactive question, a Codex permission wait, or access to independently owned terminals. Headless permission requests are denied rather than held open in the tested `claude -p` mode; answering requires a new turn that may retry the denied operation. Ordinary text questions have no machine-readable request identity in the observed Codex event stream. A Stop hook or `agent-turn-complete` cannot distinguish an input request from normal completion. No finite sample proves hook reliability across runtime versions; hooks must be installed before the session starts. Do not promise automatic delivery to discovered sessions or exactly-once effects from this evidence alone.

The regression check is `uv run --frozen pytest -q tests/integration/test_input_spike_artifacts.py`; it checks artifact coverage and the real PTY's query replies, dimensions and controlling-terminal setup without invoking either paid runtime. At the checkpoint, `python3 scripts/checks/input_detection_delivery.py` runs all seven commands sequentially for evidence review and needs local authentication. A deadline means an inconclusive probe if the stated evidence has not arrived; `waiting_at_capture` alone never proves an input request. The seven-second Claude permission wait and successful approval were repeated twice on home with the same hook sequence.
