# Fleet ownership through business modules

**Status:** Proposed design companion to [ADR 0008](../adr/0008-business-ownership-boundaries.md). This describes intended ownership, not the current package layout.

## The model

A business module owns a reason for change, a vocabulary, legal transitions, and a public command/query contract. “Controller” names the in-process composition and transaction boundary. SQLite, the repository writer, SSH, the web server, the CLI and the browser are ways to persist or deliver that behavior, not business owners.

The proposed Python layout and migration map are in [Directory structure for Fleet business modules](business-module-directory-structure.md).

The write path is `actor → CLI/web/activation adapter → module command → transaction and ports → storage or worker`. The read path is `module records → shared read projection → CLI/deck`. Worker and collector messages enter as observations; modules decide their effect on accepted state.

## Ownership map

| Module or boundary | Owns | Key rule and public behavior |
| --- | --- | --- |
| Workspace | Project identity, host links, floors, capacity, focus, shutter state | Stable project IDs; capacity and floor allocation are transactional; shuttering blocks new claims. Host connection settings remain machine config. |
| Work | Work items, relations, conditions, next steps, criteria, criterion results, accepted progress | A work item cannot become complete merely because a run ended. Checked evidence, judged authority and user acceptance are distinct. |
| Execution | Actions, claims, run intent/status, input delivery to runs, attributed usage | One active claim per action; idempotent dispatch; an unknown outcome retains its claim; only reconciled evidence permits failure with reason `lost`. |
| Authority | Actor identity and permissions, role activation, mandate-version binding, command authority | Commands carry actor and activation context. Mandate text is a versioned Record; the exact version used is retained. |
| Decisions | Decision requests, proposals and immutable accepted answers | An accepted answer has one structured owner and cites actor, mandate version and evidence. Proposal creation is explicit. |
| Attention | Actionable questions, blockers, alerts, deduplication and resolution | Attention points to a decision request when appropriate; it is not a second answer record. |
| Library | Index entries, source links, canonical locations, availability and current/historical flags | Indexing does not copy, edit or grant access to a document. |
| Observations | Latest sourced value, timestamp, freshness period and retained observation history | Stale or absent readings cannot be displayed as current health. Worker status is input to Execution, not a second run record. |
| Records authoring | Fleet-owned document identity, path, revision, author and source-run provenance | One canonical body in the management repository; commits go through one serialized writer. Working summaries move here at the planned cutover. |

Decisions and Attention may share an implementation package initially if their public contracts and record ownership remain distinct. Records authoring may start as a small workflow rather than a full package. A table, a transport, or a display type alone is not a reason to create a business module. Inside a module, domain code owns meaning and invariants, application code owns commands and ports, and a facade is the sole public behavioral entrypoint. Adapters and composition stay outside the module; another module uses only its public contract.

## What sits outside the modules

| Part | Responsibility |
| --- | --- |
| Controller composition | Construct and inject adapters and collaborator facades. The SQLite adapter supplies transaction boundaries and appends ordered state-history entries; module application workflows coordinate business commands. |
| Projections | Build read-only state documents for the deck and `fleet status`: progress, freshness, one attention lantern per place with count, missing follow-up and change markers. |
| CLI and web server | Parse requests, supply actor context, call public commands or projections, format responses. Web also serves HTTP/SSE and applies document-reader path policy. |
| Ingester and transport | Maintain web-hosted streams for now, replay worker reports, and send idempotent create/start/stop/input commands. CLI has a command transport even without the web server. |
| `fleetd` and collectors | Own host-local jobs, raw traces, runtimes and source aggregation. Report observations; retain local copies while the controller is unavailable. |
| Repository writer | Serialize and confirm commits requested by Records authoring. It implements a mechanical Git port and does not decide whether a document change is allowed. |
| Deck and plugin display | Render projections or plugin-specific observations; send user commands. Personal last-visit and saved presentation choices use controller commands when they must follow a person across devices. |

## A dispatch and recovery example

1. A user or authorized role invokes dispatch with actor, activation if present, work scope, and a stable request key.
2. Authority checks the actor and mandate version; Workspace supplies shutter state; Work supplies the target work context. Execution creates or returns the action claim and run intent in one transaction.
3. The transport sends `create` and `start` by run ID. A repeated request or lost reply returns the existing intent; fleetd rejects a changed payload for that run ID.
4. The worker reports status, result and usage. The ingester calls ingestion commands. Execution reconciles the report; an unreachable worker leaves the outcome unknown and the claim held.
5. Work checks named evidence and authority before changing a criterion. Decisions records an accepted answer when one is made. Attention holds only unresolved requests. Projections assemble the project view without asking the deck to infer completion.

## Questions the review resolves

- **Decision record versus decision document:** Decisions owns the structured answer. A narrative ADR or dossier in Git may cite its ID and supply context.
- **CLI writes and SSE:** state-history entries receive an increasing sequence in the same SQLite transaction; the web process tails that sequence.
- **CLI without web:** persistent observation capture is unavailable until the ingester runs, but CLI command transport can still dispatch and workers can retain results locally.
- **Checked criteria:** the Work module checks the required recorded evidence and outcome. Test execution stays with the worker/runtime; evidence inspection stays behind the owning module's public contract.
- **Summary cutover:** store-owned working summaries migrate once to Fleet-owned repository records. After cutover, the store retains path and revision metadata, not a writable body.

## Alignment needed before implementation

The proposed decision-record location narrows the broad “decisions in Git” wording in [ADR 0002](../adr/0002-authoritative-project-home.md), [ADR 0003](../adr/0003-agent-authorship-of-project-records.md), and the [workspace hierarchy](workspace-hierarchy.md). [The first-workspace plan](first-working-workspace-plan.md) currently writes accepted summaries and decisions to Git in Phase 3, while the ownership draft defers summary-body migration to story 7. Once that milestone is fixed, update the plan and glossary to distinguish structured decisions, narrative documents and summary bodies. ADR 0008 states the proposed ownership now so implementation does not depend on that editorial timing.
