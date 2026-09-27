# Answer delivery

FS-015 uses the spike's headless resume mechanism for Fleet-owned jobs.
Answering a question associated with a linked run records an Execution delivery
in the same transaction as the decision. Runtime-hook questions locate their
linked run by host and job. Independently discovered interactive sessions remain
manual: Fleet does not own their terminals.

The decision ID is the delivery key. The version-1 `fleetd deliver` command reads
the answer from stdin and records the key on a job step under the job lock.
Repeated keys retain that step; a changed answer is rejected. The ordinary runner
resumes the job's session for that step. A running headless turn must exit before
the answer step can be added. Delivery is acknowledged when the step has started,
not when its resulting work has succeeded.
While the existing turn is running or the answer step has not started, the worker
returns a version-1 `busy` acknowledgement. This leaves delivery pending without
counting a failure, writing delivery history or raising an alert.

Transport failure leaves the accepted decision intact and the delivery pending.
The existing web stream ingester retries on reconnect or changed host/job state;
unchanged heartbeats do not retry or write history. After three failed attempts,
Execution raises one Attention alert on the run. Further reconnects still retry;
successful delivery resolves that alert. CLI answers attempt delivery immediately
without requiring the web server, while reconnect ingestion requires it.
The ingester releases its stream-state lock before attempting delivery so a slow
transport does not block other host updates or live-update clients.

At the checkpoint, install this worker on carbon, choose a stopped Fleet Claude
job with a session ID, then run:

```sh
scripts/checks/answer-delivery.sh JOB_ID
```

This sends a harmless acknowledgement prompt twice with one key, then checks one
answer step, the unchanged session ID, and Claude's acknowledgement. It uses the
configured carbon host and bounds waiting to 90 seconds. It is not a gate and was
not run during implementation.
