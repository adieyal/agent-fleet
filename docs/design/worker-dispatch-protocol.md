# Worker dispatch protocol 3

Controller dispatch sends `create --id <job> --run-id <run> --fingerprint <sha256>
--schema-version 3 --hold`, followed by `start <job>` with the same run identity,
fingerprint and schema version. The fingerprint covers the stored dispatch payload,
including context references. The worker also hashes its immutable job definition,
so a caller cannot reuse a fingerprint with changed steps or runtime options.

Create serializes registration of run IDs. Start records a reservation under the
job lock before launching outside that lock. Repeated commands return the existing
job; a changed identity or fingerprint is refused. Legacy jobs without run identity
retain the existing command interface. Stream hello now reports protocol version 3.

After an uncertain create or start reply, the controller sends
`reconcile <run> --fingerprint <sha256> --schema-version 3`. An unreachable or missing
run keeps its unknown outcome and claim. A queued job with `start_requested: false`
can be started after reconciliation; a reserved start is never sent again blindly.
Repeating an unknown dispatch reconciles the same run instead of creating another.

A worker reports `lost` when an agent is killed without a result, or when both the
recorded runner and agent processes are confirmed gone during observation. A missing
PID or a surviving agent does not prove loss. Execution records failed with reason
lost and releases the claim; repeated identical observations add no history.
