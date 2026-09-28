# Assign Fleet facts to business modules

**Status:** Proposed for review

## Context

[ADR 0007](0007-execution-control.md) gives Fleet one transactional coordination authority for work and execution. That does not mean one controller package should contain every policy, command, and table. The CLI, web server, and workers currently hold pieces of project state and presentation logic. As persistent work is added, a shared store alone would leave the meaning and write authority of each fact unclear.

Earlier documents also use “decision” for both an accepted answer and a narrative document. [ADR 0002](0002-authoritative-project-home.md) places decisions in the management repository, while ADR 0007 calls for structured completion decisions in the store. These are different records and need separate canonical owners.

## Decision

Fleet will organise shared business behavior into cohesive modules inside the in-process controller. Each module owns the meaning, invariants, commands, and authoritative records for its facts. The controller composes those modules over one transactional store; it is a coordination boundary, not a second owner of their facts. CLI, web, and role activations invoke the same public module commands with actor and, where applicable, activation and mandate-version context. They do not write shared tables or management repositories directly.

Every persisted fact has one authoritative writer. A source may differ from that writer: fleetd reports that a process ended; Execution reconciles the report into a run state. Copies and derived values identify their source and freshness. Presentation reads projections and sends commands; it does not infer business status from raw worker fields.

The initial business boundaries are Workspace (project identity, host links, floors, capacity, focus and shuttering), Work (work items, criteria and accepted progress), Execution (actions, claims, runs, input delivery to runs, and attributed usage), Authority (actor identity and permissions, role activations and mandate-version bindings), Decisions (decision requests and accepted answers), Attention (actionable interruptions), Library (canonical-location index and availability), Observations (latest sourced values and retained history), and Records authoring (Fleet-owned documents and provenance). These names describe ownership, not a required one-package-per-table layout. A boundary may be implemented with few files at first; it must still have one public behavioral entrypoint and private policy and persistence details. [The business-module design](../design/business-module-ownership.md) gives the fact map and command flows.

Structured decision records, including question, answer, actor, evidence and mandate version, belong to Decisions in the controller store. Narrative decision documents may cite a decision ID and belong to the project management repository. The document never becomes the authority for the structured answer. This refines the use of “decisions” in ADR 0002 and [ADR 0003](0003-agent-authorship-of-project-records.md); their repository rule continues to apply to authored narrative records.

Fleet-owned document bodies have one canonical repository location. The Records authoring workflow owns document metadata and provenance and uses a single serialized repository-writer adapter for commits. The Library indexes their paths and revisions alongside product-repository, worker and external records; indexing grants neither ownership nor access. Mandate text remains a versioned management-repository record; Authority binds an activation to the exact version it used. Working summaries may remain store-owned until the planned repository migration, then move in one cutover with no two writable copies.

## Boundary rules

- A module's domain code decides valid states and transitions. Its application code coordinates commands and ports. Storage, Git, transport, runtime, HTTP and CLI code are adapters outside business modules. Other modules use the owner's public contract, not its tables or internal objects.
- Cross-module operations that require atomicity use one controller transaction. In particular, Execution checks Workspace's shutter state before claiming an action and records the claim and run intent together. The coordinating application workflow uses public module operations within that transaction; neither module reads the other's private tables.
- Work owns criterion definitions and whether a criterion is met. For a checked criterion it verifies that the named recorded evidence exists and, where specified, carries the required recorded result; it does not execute tests or evaluate general rules. The recorded-result comparison refines ADR 0007's presence check; it remains a comparison against a declared value, not a rule engine. Judged and user-accepted criteria use Authority to check the actor's mandate or user role. Run completion alone never completes work.
- Execution reconciles worker observations into run outcomes. A timeout is not evidence of failure; a run with an unknown outcome keeps its claim. A worker-confirmed missing process without a result records the run as failed with reason `lost` and releases the claim, as ADR 0007 defines.
- Out-of-authority commands return an explicit rejection. An actor may submit a well-formed proposal instead; that workflow records the proposal and raises an attention item. An invalid command does not silently become an accepted proposal. This refines ADR 0007, where a decision outside the orchestrator's authority becomes a user attention item: the attention item now follows an explicit proposal, not the rejected command.
- Projections are read-only queries over module-owned records. They compute progress, freshness, roll-ups, change markers and deck/CLI state once, and carry unknown or stale state explicitly. Renderers may format but not reinterpret them.

## Process and adapter placement

The controller remains an in-process library over SQLite; no long-running controller process is required for manual activation. The web server may host the first persistent `fleetd stream` ingester. That means observation ingestion pauses when the server is down, even while workers keep local records and finish existing runs. On reconnection, the ingester replays or reconciles observations before calling module commands. CLI dispatch still needs its own usable command-transport adapter when the web server is absent. A future background ingester can reuse the same ingestion commands without moving business rules.

The worker owns host-local jobs, traces, runtime invocation and idempotent create/start by run ID. It reports facts and does not claim actions, accept work, or commit repositories. Plugin collectors aggregate their own sources on hosts; their displays render plugin data in core-provided slots. Core stores the latest plugin observation through Observations and projects its freshness. This refines [ADR 0006](0006-display-plugins.md), whose server-side latest-value cache is an adapter or projection rather than a second accepted record.

The state-history sequence is infrastructure for ordered change notifications, not a new business owner. Each successful write appends a sequence entry in the same transaction as its state change. The web server can tail that sequence for SSE, including writes from a separate CLI process.

## Consequences

- Module boundaries can be tested through their public commands and fake ports; CLI and web behavior should agree because they use those commands and projections.
- One SQLite store makes cross-module atomic commands and an ordered change log possible, but its shared database does not permit cross-module table access.
- Repository commits and SQLite transactions cannot be one atomic operation. The authoring workflow must record an idempotent intent and reconcile the committed revision or a failed write; callers must not report a revision as accepted before it is confirmed.
- Existing descriptions of repository-held “decisions” and phase timing for summaries need wording updates when this proposal is accepted. This ADR does not claim the boundaries are already implemented.
