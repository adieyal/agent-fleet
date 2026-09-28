# Activation commands

`open_authority()` composes the activation command facade. `activate` records
the actor, orchestrator role, exact work item scope, mandate path and confirmed
Git commit. Later mandate edits affect new activations; existing activations
continue to read their recorded commit through Records.

The mandate's `decision_authority` list grants the command names
`update_progress`, `raise_attention`, `dispatch`, `summary`, `record_decision`
and `accept`. Progress updates may change
the next step, condition and resume condition. Completion additionally requires
`accept` authority and a nonempty set of met criteria. Judged criteria are granted by
criterion ID in `criteria_it_may_judge`. Checked criteria still require Work's
recorded evidence checks. Accepted criteria remain reserved for the user.
Constraints and escalation conditions are instructions to the orchestrator;
they are not executable policy expressions.

Mandates are JSON objects with all five fields shown below. This complete example
allows routine commands without granting judgement of any criterion:

```json
{
  "goal": "Implement the work item and leave its evidence ready for review",
  "constraints": ["Do not deploy"],
  "decision_authority": [
    "update_progress", "raise_attention", "dispatch", "summary", "record_decision"
  ],
  "escalation_conditions": ["Ask the user before changing the agreed scope"],
  "criteria_it_may_judge": []
}
```

`decision_authority` accepts exact command names from Authority's public
`DECISION_AUTHORITY_COMMANDS` list. `write_mandate` rejects unknown names and
reports both the unknown entries and the valid names. Prose such as
`"Dispatch actions for slice 6 tasks"` is not a command name. Add `accept` only
when delegating completion as well as routine progress updates.

To delegate judgement, put existing criterion IDs from the target work item in
`criteria_it_may_judge`, rather than criterion text. `fleet orchestrate` checks
every ID against that work item when resolving the mandate, before recording
an activation or dispatching a run. An ID from another work item is rejected.

Activation commands carry `actor` and `activation`. Work's `set` and `meet`
and Execution's `dispatch` accept that context and check Authority; Authority
does not provide duplicate entry points for these writes.
Existing manual commands retain their user-facing contract. Activation scope
is the exact work item, not implicit authority over every descendant.

Rejections raise `AuthorityRejected` without recording a proposal or attention
item. Explicit `propose` requires a question, proposed change and reason. It
records the proposal in Decisions and raises one attention item in the same
transaction. `DecisionsFacade.proposals()` returns the structured proposals;
the attention item's context reference identifies the proposal.

Start explicitly with `fleet orchestrate WORK --mandate PATH --host LOCAL
--runtime codex --cwd DIRECTORY`. The host must be configured without SSH.
The mandate path refers to a confirmed record in the project's management
repository. Runtime and directory retain dispatch's explicit inputs.
`--permission` retains `fleet send` semantics; the selected runtime permissions
must allow the local controller store and management repository to be written.

The run receives its pinned mandate and `fleet control ACTIVATION COMMAND JSON`.
Commands are `state`, `progress`, `meet`, `attention`, `dispatch`, `decide`,
`summary` and `propose`; the prompt lists their JSON fields. Actor and scope
come from the activation. Summaries and narrative decision documents are
committed through Records with the orchestrator's source run. Decisions keeps
the immutable structured choice, including activation and mandate version.
Execution completion never calls Work acceptance. There is no automatic trigger.
