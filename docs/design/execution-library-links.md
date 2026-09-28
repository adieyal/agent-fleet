# Execution links and the Library index

`fleet run link <host> <job> <work-item>` records one linked action and run.
Repeating the same host/job/work item returns that run; linking the job to a
different work item is rejected. Linking is offline: runtime, timestamps and
outcome remain unknown until observed. Run links never change accepted work.

`fleet send ... --work-item <id>` validates the work item before dispatch and
records the link immediately after job creation, before context upload or start.
The selected runtime is recorded; creation alone does not establish a running
status. A later upload or start failure leaves the link intact.

`fleet library link <url> --work-item <id>` indexes an HTTP(S) reference in the
work item's project. Without a work item, supply `--project <id>`. An optional
`--title` supplies a label; omitted titles are stored and returned as JSON `null`. Entries are
current external references, with no associated run. Nothing is fetched,
copied or granted access. Existing local `library add` and `library rm` remain
separate commands.

Link commands print the stored record as JSON and accept `--actor` (the manual
CLI actor is `user`). The public Execution and Library facades expose the
persisted records for subsequent projections. Stream reconciliation and output
indexing are separate stories.
