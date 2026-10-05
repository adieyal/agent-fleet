# Stored attention (FS-004)

`fleet.composition.open_attention()` constructs the public `AttentionFacade`.
Commands are `raise_item`, `acknowledge`, `snooze`, `reopen`, and `resolve`; queries are
`get` and `list`. Records are immutable typed values. Commands require an actor.
Source and source reference identify one item; repeating a signal refreshes
its metadata and last-seen timestamp while preserving its state and ID.

An expired snooze is returned as open by queries without writing to SQLite.
The stored snooze and its deadline remain the durable facts. Subsequent commands
record transitions from the stored state, so history remains a chain of explicit
writes. Each write, including a repeated signal, records actor and state history
in the same transaction.

The legacy workspace file contains only item IDs and attention actions, without
project, kind, owner, headline or context. Import preserves those actions in
`attention_imported_action`, keeps the original file and a `.json.bak`, and records
completion transactionally. When ingestion first raises an item with a matching
`source_reference`, that action supplies its initial state. Stream ingestion uses
the old occurrence ID as the source reference to retain existing acknowledgements
and snoozes. Workspace writes no longer store attention actions.

The web server passes host observations to Attention on stream updates. Job failures
and stalls become blockers; AskUserQuestion and ExitPlanMode become decisions.
`stream:<host>` and the occurrence reference identify an item. Typed stream context
preserves the deck's owner, summary and project fields; its context reference points
to the source job or session. Acknowledgement and resolution timestamps are stored.

Each job/session message reconciles only that owner. The first heartbeat follows
fleetd's initial job and session scans, so it reconciles missing owners after a
reconnect without resolving items prematurely on `hello`. Unreachable hosts cause
no attention writes. The read-only projection marks their stored items stale and
includes the retained last-seen time, including before the first report after restart.
Fixture servers ingest once into their own temporary store, isolated from live state.

CLI output is JSON. For example:

```sh
fleet attention add 'Choose a direction' --project p1 --kind decision --owner user \
  --source manual --source-reference question-1 --context-reference doc:1 --actor user
fleet attention list --project p1 --state open
fleet attention ack ITEM-ID --actor user
fleet attention snooze ITEM-ID --until 2026-10-01T09:00:00+02:00 --actor user
fleet attention resolve ITEM-ID --details 'Answered in session' --actor user
```
