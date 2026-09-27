# Linked run reconciliation

The web-hosted ingester feeds job observations into Execution by host and job ID.
The existing stream silence deadline is 20 seconds. Disconnects and silence mark
running runs as unknown outcome; known terminal outcomes remain recorded. A hello
alone does not establish a run outcome: the worker must report the job again.
Streams include retained jobs beyond the default 24-hour window so a long outage
does not hide a completed linked job on reconnect.

Execution maps done to succeeded and cancelled to stopped. Queued and stalled
jobs remain unknown outcome. Fleetd's current stalled status checks only the
runner PID, not whether the agent process is gone without a result. Confirmed
failure with reason lost is deferred to FS-018/FS-019; silence and stalled are
never sufficient evidence. Reconciliation does not mutate Work.

Stream protocol version 2 adds trace path and availability to job summaries and
announces protocol_version in hello. The trace is the worker's normalized
events.jsonl. Its removal changes the existing file signature and emits another
summary. Older workers omit trace metadata; the controller does not invent an
available trace. Reinstall fleetd to receive trace availability observations.

Library entries reference worker paths as fleet://host/absolute/path, with the
host and path URL-escaped. Report and trace identities are stable for a run and
canonical location. Pruning changes availability without removing the entry.
The library copies no output bodies. Last-observed time retains the timestamp
on the worker job observation; unchanged heartbeats do not create history.
