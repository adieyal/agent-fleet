# Activation commands

`open_authority()` composes the activation command facade. `activate` records
the actor, orchestrator role, exact work item scope, mandate path and confirmed
Git commit. Later mandate edits affect new activations; existing activations
continue to read their recorded commit through Records.

The mandate's `decision_authority` list grants the command names
`update_progress`, `raise_attention` and `dispatch`. Progress updates may change
the next step, condition and resume condition. Judged criteria are granted by
criterion ID in `criteria_it_may_judge`. Checked criteria still require Work's
recorded evidence checks. Accepted criteria remain reserved for the user.
Constraints and escalation conditions are instructions to the orchestrator;
they are not executable policy expressions.

Activation commands carry `actor` and `activation`. Work's `set` and `meet`
and Execution's `dispatch` also accept that context and check Authority.
Existing manual commands retain their user-facing contract. Activation scope
is the exact work item, not implicit authority over every descendant.

Rejections raise `AuthorityRejected` without recording a proposal or attention
item. Explicit `propose` requires a question, proposed change and reason. It
records the proposal in Decisions and raises one attention item in the same
transaction. `DecisionsFacade.proposals()` returns the structured proposals;
the attention item's context reference identifies the proposal.
