# Give executable actions one coordination authority

**Status:** Proposed for review

## Decision

Fleet's first persistent work system will have one controller that coordinates projects, work items, executable actions, and agent runs across hosts. A work item may have several actions and parallel runs. Each action names one concrete intended execution, has a stable ID, and may have only one active claim. An agent run is one attempt to carry out an action on a host; it does not own or complete the work item.

The controller will use a local transactional store for structured workspace state and coordination: project identity and slot assignment, work items, actions, claims, run links, attention, and library metadata. CLI and web operations will use the same store. Host connection settings remain configuration. Project documents retain one canonical location: the project's management repository when Fleet owns them, a product repository or worker when appropriate, or an external resource such as Google Drive. The library indexes those locations and their availability. This refines [ADR 0002](0002-authoritative-project-home.md): the logical project home includes the controller's structured state and the project's authored records, with one owner for each fact.

The first dispatch source is an explicit user command. It identifies the actor and work scope, selects or creates an action, claims it, dispatches a run, and records the outcome and evidence. When an orchestrator is added, its activation also resolves a role and mandate version, then updates the work item or raises a question within that authority. Automatic schedules and a general wake queue are later additions to the same protocol.

## Why this decision is needed now

Fleet already dispatches ordered steps to Claude Code or Codex on a host, but a host-local job is not a durable work item. Today `fleet send` creates a remote job and then starts it; the controller has no action record that says why the run exists or whether a retry is the same request. The dashboard joins host observations with project identity and user choices, but it cannot reliably answer what is complete or what should happen next. [The domain description](../../CONTEXT.md) defines the persistent work these runs should serve.

Paperclip demonstrates atomic task checkout and coalesced wakeups ([task workflow](https://github.com/paperclipai/paperclip/blob/master/docs/guides/agent-developer/task-workflow.md), [runtime guide](https://github.com/paperclipai/paperclip/blob/master/docs/agents-runtime.md)). Fleet needs the execution guarantees while preserving parallel work on one epic and roles that persist between runs. Claiming the **action** rather than the work item is the distinction that permits both.

## Execution contract

- **Identity:** `project_id`, `work_item_id`, `action_id`, and `run_id` are distinct. A run records its host and remote job ID. A run may target an epic or any smaller work item through its action; work-item nesting does not control dispatch.
- **Claim:** claiming an unclaimed action and recording the intended run happen in one controller transaction. A second claimant receives a conflict or the existing run reference. It may still claim a different action under the same work item. The claim is held until its run reaches a known end (succeeded, failed or stopped); the action may then be attempted again by a new run. Claims have no expiry.
- **Retry:** a dispatch request carries a stable idempotency key. Repeating it returns the same action and run intent. The remote create/start protocol must also recognise a repeated run ID and reject a changed payload. Fleet does not treat a lost SSH response as evidence that a run did not start.
- **Recovery:** a host that cannot be reached makes its run outcome **unknown**. The controller reconciles the remote job by ID when contact returns. It does not call the run failed or reassign its action solely because a timeout elapsed. When the host is reachable but the run's process has ended without recording a result, reconciliation records the run as failed with reason `lost`, releasing its claim so the action may be retried. A run is `lost` only when the worker can confirm the process is gone; otherwise its outcome stays unknown. Any future automatic takeover needs a way to stop or fence the old attempt before another starts.
- **Acceptance:** run completion and work-item completion are separate transitions. A completion decision records the actor, mandate version, evidence, and outcome. Each completion criterion declares how it is met: **checked** (Fleet confirms named evidence exists, such as a recorded test result or library artifact), **judged** (the orchestrator decides within its mandate), or **accepted** (the user decides). The orchestrator may accept completion only within delegated authority and never for a checked criterion without its evidence or one reserved for the user; a decision outside that authority becomes a user attention item. The first version enforces these identity, evidence, and authority checks, without a general rule engine: a checked criterion is a presence check on recorded evidence, not an evaluated rule.
- **Availability:** workers may finish and record already-dispatched runs while the controller is unavailable. They cannot create new claims for shared work until the controller returns. This gives a clear first-version guarantee rather than promising exactly-once execution during a network partition.
- **Shuttering:** a shuttered project retains its identity, work, and library. Existing runs may finish; new action claims for that project are refused until restoration.

The controller is the only writer of shared claims. Its initial implementation can be a SQLite database used by local CLI and web processes. The transaction, uniqueness, and migration behavior matters more than whether a long-running controller process exists; none is required for manual activation.

## First implementation boundary

The first release must support one project with flexible work items, explicit actions, runs on two hosts, a useful project summary, a library of outputs and references, and an orchestrator decision that either stays within its mandate or reaches the user. It must show failed and unreachable runs without turning either into an unsupported conclusion about the work item.

It does not need a durable wake queue, periodic heartbeats, routines, automatic lease expiry, a configurable review pipeline, budgets, arbitrary runtime plugins, or full offline multi-writer synchronization. These can build on the identities and transitions above when a real trigger or runtime requires them. [Paperclip's routine policies](https://docs.paperclip.ing/guides/projects-workflow/routines/) are useful later, when Fleet has schedules and missed activations to reconcile.
