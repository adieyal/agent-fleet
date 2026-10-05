# First working Fleet workspace: implementation plan

**Status:** Proposed for review. This plan describes future implementation, not features already shipped.

## Target outcome

Open one project that has an epic worked on by agents on two hosts. Fleet should answer what is underway, what criteria are complete, what happens next, which run failed, what artifacts the work produced, and which decision needs the user. A run finishing must not silently complete the epic. The orchestrator should resolve routine decisions within its mandate and interrupt the user only when a decision exceeds that authority.

The execution rules are in [ADR 0007](../adr/0007-execution-control.md); the domain terms are in [CONTEXT.md](../../CONTEXT.md). The first useful read view arrives before automatic orchestration. Each phase below leaves a reviewable, working increment.

## Phase 0: establish one write authority and safe document access

**Deliverable:** CLI and web changes to project identity, floors, and other shared workspace choices no longer race through separate whole-file JSON writes. The new structured state has a migration path from existing `config.json` and `workspace.json` data.

- Add a local SQLite store with schema migrations and transactional commands. Move project records and host-label links, floor assignment and shutter state, focus, and attention actions into it. Keep host connection settings and other machine configuration in `config.json`; make remaining config writes atomic and serialized.
- Migrate existing user data once, retaining a backup and a read-only rollback path until the new store is verified. Define which fields are authoritative in SQLite so CLI and web do not keep two writable copies.
- Make move-in, merge, shutter, and restore single transactions across project and floor state. Preserve the six-floor default, ten-floor maximum, and stable floor assignment.
- Restrict recorded-document reads to explicitly approved roots or copied job artifacts. Reject traversal and symlink escape. Keep the dashboard on loopback; before allowing a shared-network bind, add authentication for reads and writes. A library link to an external document grants no access on its own.

**Gate:** migration preserves IDs, links, floors, focus, and attention; concurrent CLI/web edits cannot lose one another; a failed transaction changes nothing; an arbitrary recorded path cannot be read through the dashboard. Existing project and building behavior still works.

## Phase 1: make work legible before automating it

**Deliverable:** a durable project view can answer the five work questions even when no agent is currently running.

- Add work items with stable IDs, kind, goal, optional parent and typed relations, status, completion criteria or milestones, progress evidence, dependencies, next action, and timestamps. No fixed epic-to-task depth is required. Do not derive a percentage without a known denominator.
- Add a project library index. Each entry records project, optional work item and run, kind, title, source, canonical location, availability, and current or historical state. Start with local documents, worker reports/traces, images or other files, and external URL references. Fetching or editing Google Drive content is outside this phase.
- Register the project's management repository when Fleet has authored documents to keep. Keep a document's canonical copy there when Fleet owns it, while indexing product-repository, worker, and external sources by reference. Do not copy a raw trace into Git merely to make it appear in the library.
- Record explicit decision requests and resolutions with owner, question, context, and affected work. Keep run failures separate from work-item status and from questions that require the user.
- Allow an existing host job to be linked to a work item by `(host, remote job ID)`. Build a plain CLI summary and a simple dashboard read view showing underway, done against criteria, next, failures, and attention. Rich spatial presentation can use the same projection later.

**Gate:** restart Fleet with both hosts offline and the project still shows its goal, accepted progress, next action, library, and unresolved question. Returning host observations update run status without erasing those records.

## Phase 2: execute one action reliably across hosts

**Deliverable:** a user can dispatch one action for a work item and get one traceable run, even if a dispatch response is lost or the host disconnects. Orchestrator activation follows in Phase 3.

- Add `Action` and `Run` records using the identities and claim transaction in ADR 0007. Keep the action's target independent of its work item's depth. Record dispatch reason, actor, selected host/runtime, payload fingerprint, and attempt history. Add role and mandate version when orchestrator activation arrives in Phase 3.
- Reuse the existing SSH transport and host runner. Pass a controller-generated run ID to `fleetd`; make remote create/start idempotent for that ID and refuse a different payload under it. Reconcile by remote ID after uncertain responses.
- Add an explicit run state for unreachable or unknown outcome. Show last observation time. Reconcile after reconnection; never restart an uncertain attempt automatically. Permit manual retry only after the old attempt is known stopped or an operator explicitly resolves uncertainty.
- Capture token or usage figures when the runtime reports them, with their source and an explicit unknown value otherwise. Cost conversion and budget enforcement remain deferred.
- Keep Claude Code and Codex behind one small internal invocation/result seam where they truly differ. Do not introduce a public adapter framework yet.

**Gate:** two concurrent claim attempts for one action yield one run; two different actions on the same epic can run on different hosts; retrying after a lost SSH reply starts no second job; disconnection does not mark work complete or failed.

## Phase 3: let an orchestrator decide within a mandate

**Deliverable:** an orchestrator can coordinate the first complete work loop with few user interruptions.

- Persist a versioned mandate for its scope: goal, constraints, completion criteria, decision authority, and escalation conditions. Give each activation the version it used.
- Record progress updates and decisions with actor, run, evidence, and mandate version. Allow the orchestrator to accept evidenced completion within its authority. Route disputed criteria, changed goals or priorities, and out-of-authority decisions to the user as attention items.
- Write accepted summaries and decisions that Fleet authors to the project's management repository with source-run provenance. Serialize those writes; the structured store records their canonical paths and current revision.
- On each outcome, leave a next action or a named waiting/blocked condition. A failed run should produce a concrete recovery action or an explicit escalation, not a silent terminal state for its work item.
- Exercise the complete scenario from the target outcome through the CLI and a minimal dashboard view, including a project library entry and a user decision that unblocks work.

**Gate:** a reviewer can answer the five target questions from persisted state; routine decisions are recorded without prompting the user; an out-of-authority decision cannot be accepted by the orchestrator; a successful run cannot close work without the required evidence and authority.

## Implementation map

The controller store should be a small module with transactional commands, used by both `packages/fleet-cli/src/fleet_cli/cli.py` and `packages/fleet-web/src/fleet_web/server.py`. Migrate the registry behavior in `fleet/projects.py` and workspace choices in `fleet/workspace.py` to that interface; keep read projections separate from mutations. Work items, actions, mandates, runs, decisions, and library entries should have one domain owner each, even if the first implementation uses a few small modules. Extend `packages/fleet-worker/src/fleet_worker/fleetd.py` only for run identity, idempotent create/start, and richer observations; keep its single-file host installation path. Adapt the web state projection after the CLI and store can complete the scenario.

The migration must read old files without silently deleting them. Tests should cover current config import, concurrent CLI/web changes, crash points around dispatch, reconnection, evidence-backed acceptance, and the five-question project view. Run focused tests at each phase, then the repository's broader checks. The final proof should use a local and an SSH host for real dispatch and a controlled offline-host scenario for uncertainty and recovery.

## Add later, when there is a concrete trigger

| Capability | Add when |
| --- | --- |
| Durable wake queue, coalescing, priorities | More than one independent event source activates roles or activations must survive a controller restart. |
| Schedules, webhooks, routine overlap and catch-up policies | A recurring responsibility is ready to run unattended. Default to coalescing active work and skipping missed ticks unless that routine needs another policy. |
| Automatic claim expiry and orphan takeover | A safe stop or fencing mechanism can prove the old attempt cannot still act. |
| General completion gates and independent review chains | A real class of work repeatedly needs the same machine-enforced rule. |
| Cost totals, budgets and hard stops | Runtime usage is captured with reliable attribution and pricing; record unknown usage rather than inventing a cost. |
| Public runtime adapter interface | A third runtime or external adapter needs a stable contract beyond the internal Claude/Codex seam. |
| Distributed multi-writer project sync | Workers must author shared project state while the controller is offline. Existing runs may continue without this. |

The rich building view can be developed against these records, but it does not gate the first working loop. Keep host and run details inspectable while the project summary stays focused on progress, next actions, failures, and decisions.
