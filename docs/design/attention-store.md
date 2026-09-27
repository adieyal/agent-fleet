# Stored attention (FS-004)

`fleet.composition.open_attention()` constructs the public `AttentionFacade`.
Commands are `raise_item`, `acknowledge`, `snooze`, and `resolve`; queries are
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
`source_reference`, that action supplies its initial state. FS-005 should use the
old occurrence ID as the source reference to retain existing acknowledgements and
snoozes. No item metadata is manufactured. The legacy deck remains on its existing
path until FS-005 moves its callers.

CLI output is JSON. For example:

```sh
fleet attention add 'Choose a direction' --project p1 --kind decision --owner user \
  --source manual --source-reference question-1 --context-reference doc:1 --actor user
fleet attention list --project p1 --state open
fleet attention ack ITEM-ID --actor user
fleet attention snooze ITEM-ID --until 2026-10-01T09:00:00+02:00 --actor user
fleet attention resolve ITEM-ID --details 'Answered in session' --actor user
```
