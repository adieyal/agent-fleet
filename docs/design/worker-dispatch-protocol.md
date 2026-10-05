# Worker dispatch protocol 4

Controller dispatch sends `create --id <job> --run-id <run> --fingerprint <sha256>
--schema-version 4 --hold`, followed by `start <job>` with the same run identity,
fingerprint and schema version. The fingerprint covers the stored dispatch payload,
including context references. The worker also hashes its immutable job definition,
so a caller cannot reuse a fingerprint with changed steps or runtime options.

Create serializes registration of run IDs. Start records a reservation under the
job lock before launching outside that lock. Repeated commands return the existing
job; a changed identity or fingerprint is refused. Legacy jobs without run identity
retain the existing command interface. Stream hello now reports protocol version 3.

After an uncertain create or start reply, the controller sends
`reconcile <run> --fingerprint <sha256> --schema-version 4`. An unreachable or missing
run keeps its unknown outcome and claim. A queued job with `start_requested: false`
can be started after reconciliation; a reserved start is never sent again blindly.
Repeating an unknown dispatch reconciles the same run instead of creating another.

A worker reports `lost` when an agent is killed without a result, or when both the
recorded runner and agent processes are confirmed gone during observation. A missing
PID or a surviving agent does not prove loss. Execution records failed with reason
lost and releases the claim; repeated identical observations add no history.
# Reconciliation after controller death

Reconcile query version 4 adds a successful `absent` response containing
`schema_version: 4`, `run_id` and the requested `fingerprint` when the worker
has no job for that run. Existing jobs return their usual summary with version
4. Create, start and reconcile all require version 4; version 3 is rejected.
Install the updated worker before using controller dispatch.

Only a matching version 4 absence permits the controller to create the stored
intent again, using its original run ID and payload. A timeout, disconnect or
worker error leaves the claim held and the outcome unknown. Concurrent creates
remain protected by the worker's run identity lock. Repeated reconciliation of
an existing job does not create another job or start it again.

Run `scripts/checks/phase2-gate.sh` on carbon at the checkpoint to exercise
local/SSH claims, parallel actions, a dropped create reply and disconnects.
It creates held jobs and documents the expected result at each step; automated
tests use isolated controller stores and worker directories instead.

## Wire protocol 2: descendant completion

Worker release 0.1.1 changes step completion semantics. The runner becomes a Linux
child subreaper before starting an agent. After the agent exits and its workspace
watcher stops, the runner reaps and waits for all remaining children before
returning the attempt result, retrying, or starting the next step. Orphaned
children, including double-forked processes that create their own sessions, are
adopted by the runner. Runtime success alone no longer completes a step.

For example, an agent that starts `docker build ... > build.log 2>&1 &` and reports
`FLEET_STATUS: done` leaves the step running until its descendant exits. The
existing activity field exposes `children_wait`, its PIDs and a waiting summary;
events record PID changes. There is no automatic wait deadline and no agent
replay. A service deliberately left running also keeps the step open. Agents
should run finite work in the foreground and stop services before ending a step.

Cancellation during this wait sends SIGTERM to adopted children, repeats as
further descendants are adopted, and escalates to SIGKILL after five seconds.
The runner reaps the children before returning. Descendant exit codes do not
replace the agent's reported outcome; agents must inspect their own command
results. Work submitted to an external daemon is outside OS process ancestry.
The completion guarantee requires Linux `/proc` and child-subreaper support;
unsupported workers refuse to run rather than silently losing the guarantee.

Wire protocol 2 rejects workers implementing the earlier completion behavior.
Dispatch schema remains 4 and stream protocol remains 3 because their record
shapes are unchanged. Upgrade the controller wheels and run `fleet install HOST`
for each worker before dispatching new jobs.
