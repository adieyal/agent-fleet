# Fleet domain

This describes the intended workspace model for review. It is not a claim that every part is implemented today. This file owns what the terms mean; the [product spec](docs/design/workspace-prd.md) owns how they appear in the world (floors, lanterns, the lobby, the storehouse), and the [ADRs](docs/adr/) record why.

Fleet should let one person answer five questions: **what are we working on, how much is complete, what is next, what failed, and which decisions need me?** It follows ongoing work across hosts while individual agent runs come and go.

## Core relationships

- A **project** is an ongoing workspace with a stable identity, not necessarily a bounded endeavour. It may use several hosts and repositories, or none, and may own or link to documents, datasets, images, policies and other resources. Fleet need not model every resource type initially.
- A project is either **live** or **shuttered**. Every live project occupies exactly one **floor**. The number of floors is a human work-in-progress limit, not a limit on projects, agents or hosts. Shuttering frees the floor without changing the project's identity, work or library.
- A project contains **work items**. Epic, workstream, milestone and task are common kinds, not required levels: work items nest to any useful depth and may relate across branches.
- Work is executed through **actions**. An action is one concrete intended execution for a work item; an **agent run** is one attempt to carry it out on a host. Several actions, and so several runs on different hosts, can serve the same work item at once. A run finishing does not complete its work item: completion is a separate **acceptance**, backed by evidence.
- An **orchestrator** acts as the user's proxy within a **mandate**. It refines plans, dispatches actions, accepts evidenced outcomes and records decisions within its authority, escalating only what exceeds that authority or needs the user's judgement. The aim is to make as many sound decisions as possible without interrupting the user.
- Every project has a **library** that indexes its produced and linked documents and assets. A link to an external resource, such as a Google Drive document, is not a copy or an access grant.
- A project's accepted state lives in its **project home**: structured state in the **controller**, and the documents Fleet authors in the project's **management repository**. Each fact has one owner.

## The project view

For each work item, the useful read view combines its goal, completion criteria, accepted progress, next step, dependencies, failures and open questions. A percentage appears only when there is a meaningful total; without one, progress is unknown, never zero. A failed run remains visible as evidence; its effect on the work item is a separate judgement. After each outcome, a work item is left with a next step or a named waiting, blocked or on-hold condition, never a silent dead end. Questions reach the user as attention items only when the orchestrator cannot resolve them within its mandate.

## Main interactions

1. The user establishes a project. It moves onto a free floor; if none is free, the user chooses which live project to shutter. The user sets its focus and delegates goals and decision authority through a mandate.
2. Work items describe the goal at whatever granularity is useful. The orchestrator refines plans and dispatches actions to hosts within its mandate.
3. Runs produce activity, traces, documents and proposed outcomes. Their outputs enter the project library, or are linked there with source and availability recorded.
4. The orchestrator updates progress and next steps, accepts evidenced outcomes when authorised, and records decisions. Anything beyond its authority becomes an attention item.
5. Fleet presents current work, completion, next steps, failures and attention at project and work-item level. Host and run detail remains available for investigation.

## Language

### Workspace

**Project**:
An ongoing workspace with an identity independent of its name, repository or host. It may span several hosts and repositories, or have no product repository at all. A known project ID links a host automatically; matching repository information may suggest a link; matching names alone never merge projects.

**Live project**:
A project occupying a floor. Only live projects accept new action claims.
_Avoid_: Active project (active describes running work)

**Floor**:
One unit of project capacity, shown as a storey of the workspace building. A live project keeps its floor until it is shuttered. Floors never move or change because of focus or activity.
_Avoid_: Slot as a separate concept

**Project capacity**:
The fixed number of floors: six by default, ten at most. Raising it is a deliberate settings change, never a side effect of starting work.

**Shuttered project**:
A project archived out of the building to free its floor. It keeps its identity, work and library, with documents still searchable and marked historical. New action claims are refused; in-flight runs finish and their results are recorded. Restoring it returns the project as it was, plus those results, to its old floor if that floor is free.
_Avoid_: Parked project

**Focus**:
The user's explicit choice of where to put resources: **priority** or **background**. Focus applies to projects and, within a project, to epics. Activity never changes focus. Focus is not navigation; moving between scopes is *entering* them.
_Avoid_: Focus to mean the scope being viewed; parked as a focus state; priority to mean queue order

**Space**:
A view over shared project records, arranged for a purpose such as following an active slice or monitoring a server. Spaces may overlap without copying the records they show. Agents may create draft spaces; useful definitions are retained in the management repository.

**Host**:
A configured machine where agent runs execute. A host is execution context: it explains where work runs and what a machine can do, not how projects are divided.

**Worker**:
Fleet's execution side on a host: `fleetd` and its server-local `.fleet` records of memberships, runs and traces. A worker holds copies of project state, never an independent accepted version.

### Work

**Work item**:
A persistent goal or scope of work within a project, optionally nested inside another work item or related to others, such as by a dependency. It holds completion criteria, progress, dependencies, a next step, questions, and links to actions, runs and outputs. Actions may target a work item at any depth; nesting does not control dispatch.

**Epic**:
A kind of work item: a substantial goal within a project that can contain several workstreams.

**Workstream**:
A kind of work item: an independently progressing line of work, often under an epic. Its findings may inform other workstreams.

**Milestone**:
A kind of work item: a bounded checkpoint with explicit completion criteria. A parent's progress is usually counted in its milestones. A slice is a milestone and may contain nested work, many tasks and many runs.
_Avoid_: Slice as a separate epic or workstream

**Task**:
A kind of work item: a small, concrete unit of work, usually within a milestone.

**Completion criteria**:
The explicit, checkable conditions under which a work item is done. The work item owns them. Each criterion records how it is verified: **checked** by Fleet (a test result, an artifact that exists), **judged** by the orchestrator within its mandate, or **accepted** by the user. A met criterion records who or what verified it and the evidence cited.

**Progress**:
Accepted outcomes measured against a known total, such as completed versus planned milestones or criteria met. Unknown when there is no total; never shown as zero.

**Next step**:
What is expected to move a work item forward. It may be realised as an action, but it is a description of intent, not an execution record.
_Avoid_: Next action (action is the execution record)

**Waiting**:
Work that names a resume condition, such as more data arriving. When the condition is met the work becomes ready for review; no agent starts automatically.

**Ready for review**:
Waiting work, or a shuttered project, whose resume condition has been met. It shows quietly and does not start an agent or interrupt the user, unless the user opted into that condition.

**Blocked**:
Work that cannot proceed until someone acts beyond the orchestrator's authority. A genuine blocker is raised as an attention item. Work waiting for a named condition is waiting, not blocked.

**On hold**:
Work set aside by the user inside a live project. It stays put until the user resumes it and does not wake because of activity elsewhere.
_Avoid_: Parked, for work inside a project

### Execution

**Trigger**:
An event that calls for a role to act on a scope: a user command, a met condition, a resolved decision, or later a schedule. In the first version the only trigger is an explicit user command.

**Activation**:
One response by a role to one or more triggers. It resolves the mandate version and context, selects or creates an action, claims it, dispatches a run, records the outcome and evidence, and leaves a next step or a named condition. Once triggers are queued, several triggers for the same role and scope become one activation with several reasons, not several runs.

**Action**:
One concrete intended execution for a work item, with a stable ID. Repeating a dispatch request with the same idempotency key returns the same action rather than creating another. An action may be attempted by several runs over time, one at a time. Claiming the action, not the work item, is what lets several runs serve one epic in parallel.

**Claim**:
An action's exclusive reservation for one run, made in a single controller transaction. An action has at most one active claim; a second claimant receives a conflict or the existing run. The claim is held from dispatch until its run reaches a known end (succeeded, failed or stopped). An unknown outcome keeps the claim, and no timer releases it.
_Avoid_: Lease (claims do not expire); claim for an assertion of fact or a conflicting edit (use *proposal*)

**Agent run**:
One bounded attempt, on one host, to carry out an action. It has its own status, trace, updates and outputs, and records its host and remote job ID. It does not represent the persistent work, and finishing it does not complete the work item.

**Unknown outcome**:
The state of a run whose host cannot be reached. It is reconciled by run ID when contact returns and is never treated as failed, or its action reassigned, because a timeout elapsed. A run on a reachable host whose process ended without recording a result is not unknown: reconciliation marks it failed, with reason *lost*.

**Job**:
The host-local execution record `fleetd` keeps today. A run references its job by host and remote job ID.
_Avoid_: Job for the run or for the durable work

**Run trace**:
The detailed activity emitted by one agent run. It stays on the worker under a retention policy and is not the durable project record. The library may link to it; a pruned trace stays visible as unavailable.

### Authority and attention

**Role**:
A persistent project responsibility, such as orchestrator or librarian, carried out through activations and their temporary agent runs. It remains visible and meaningful while no agent is running, with its remit, queue, last outcome and next trigger.

**Orchestrator**:
The role acting as the user's proxy for a scope of work. It coordinates agents and resolves decisions within a mandate, recording outcomes and bringing only decisions outside its authority to the user.

**Librarian**:
The role that keeps the library in order. It may make reversible documentation improvements directly in the management repository; changes to goals, accepted decisions or disputed facts become proposals.

**Mandate**:
A versioned statement, attached to a scope of work and given to a role, of the goal, constraints, decision authority and escalation conditions, including which of the scope's completion criteria the role may judge. Authority scales with risk and cost so routine choices do not interrupt the user. Every activation and decision records the mandate version it relied on.

**Acceptance**:
The transition that marks a work item complete against its completion criteria. It records the actor, mandate version, evidence and outcome, and is separate from any run finishing. Every criterion must be met in the way it declares: an orchestrator cannot accept a checked criterion without the check, or one reserved for the user. An orchestrator may accept only within its mandate; otherwise the decision becomes an attention item.

**Evidence**:
Records showing that a completion criterion is met, such as test results, reports or run outputs. A run finishing is not in itself evidence that criteria are met.

**Decision**:
A recorded choice with its actor, mandate version, context and affected work. Decisions within a mandate are recorded without interrupting the user; others begin as decision requests.

**Proposal**:
A change awaiting acceptance: an edit beyond its author's authority, a disputed completion, or an offline edit that conflicts with accepted state. Proposals are never applied by order of arrival.

**Attention item**:
A decision request, genuine blocker or actionable alert that calls for the user. It has an owner, a source, and a state: open, acknowledged, snoozed or resolved. Reading one does not resolve it. Ordinary agent activity, new reports and met waiting conditions do not become attention items merely by happening.

**Permission refusal**:
A permission request a job's agent was refused because nobody was at the prompt; the agent carries on without it. A job step's refusals form one attention item, answered by allowing permission rules for the job, which continues the refused step, or by dismissing it. When the job moves on to a later step, or is gone, with the item untouched, it resolves as refused. An interactive session's permission request is a question to the person at its prompt and stays its own item.

**Session question**:
A question an interactive session asks the person at its terminal, with the options it offers. It is an attention item so it is not missed, but it is answered only in that terminal; the item closes when the session has its answer.

### Records

**Project home**:
The logical authority for a project's identity, accepted workspace state and links to canonical records. It comprises the controller's structured state and the management repository's authored documents, with one owner for each fact. Worker copies do not independently define accepted state.

**Controller**:
The single writer of shared structured state: project identity and floors, focus, work items, actions and claims, run links, attention and library metadata. The CLI and web app both write through it. While it is unavailable, workers may finish and record already-dispatched runs but cannot claim new shared work.

**Management repository**:
A per-project Git repository for the durable records Fleet authors: the plans it owns, summaries, decisions, dossiers and retained space definitions. It is separate from any product repository and never holds raw traces or live state. Writes are serialised and carry author and source-run provenance.

**Project library**:
The project's orderly index of produced and linked assets: PRDs, plans, task summaries, decisions, run traces, images, datasets and other documents. Each entry records its source, related work, canonical location, availability, and whether it is current or historical. Indexing a resource does not copy it or grant access to it.

**Dossier**:
A durable account of completed work, such as a slice, preserving its outcome, decisions and selected evidence after the runs end. It is pinned to the records and space-definition revision in effect at closure, so later edits do not rewrite what happened.

**Executive summary**:
A concise account of a project's or work item's purpose, completed outcomes, current work and remaining work. It identifies its authoring role and update time without requiring claim-by-claim citations.

**Observation**:
A timestamped reading about a subject, with a source and a freshness period. Once that period passes, the subject's current state is stale or unknown, even if the last reading was healthy; older observations remain history.

**Plugin**:
A packaged specialised display for a space: a manifest, an optional collector that runs on hosts and emits observations, and a browser display module. Fleet core carries and mounts plugins without knowing what they mean.

## States at a glance

| Subject | States |
|---|---|
| Project | live, shuttered |
| Focus (projects, epics) | priority, background |
| Work item | waiting, ready for review, blocked, on hold, complete (accepted) |
| Agent run | running, succeeded, failed (including lost), stopped, unknown outcome |
| Completion criterion | unmet, met (checked, judged or accepted) |
| Attention item | open, acknowledged, snoozed, resolved |
| Observation | current, stale, unknown |

State changes to work items, attention items and runs are timestamped and kept for at least a week, independently of raw traces.
