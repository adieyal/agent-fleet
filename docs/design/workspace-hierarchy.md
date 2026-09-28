# Fleet workspace hierarchy: working design

This records decisions reached during the design interview. It describes the intended workspace, not current Fleet behaviour. The [project philosophy](philosophy.md) sets the design priorities, and the [glossary](../../CONTEXT.md) defines the core terms.

## Focus and presentation

- Fleet presents an overview of projects and agents across hosts without individual traces or speech bubbles. Project, epic, workstream, milestone, and agent views reveal more detail as the user enters them. Traces are for investigation and debugging, not normal navigation.
- Each scope offers an executive summary of what it is trying to achieve, what has been done, what is underway, and what remains. The summary shows its authoring role and update time and links to a report or dossier when useful, without claim-by-claim grounding.
- Projects contain work items at whatever granularity is useful: epics, sub-epics, milestones, tasks, or other project-defined kinds. Work items may nest or link across branches; agent runs may attach directly to any of them. No particular path through these kinds is required.
- The user sets focus (priority or background) explicitly for projects and epics. Activity alone does not promote a project or epic; a blocker or decision may still be highlighted. Priority is where the user allocates resources, so priority work is usually the busiest; the rule is that busy background work must not look important. Work inside a project is set aside by putting it on hold.
- The workspace has a fixed number of active project slots, shown as floors: six by default and ten at most. This is a human work-in-progress limit, not a host or execution limit. Moving a project in when every slot is occupied asks which project to shutter; raising capacity is a deliberate setting.
- Shuttering archives a project and frees its capacity. It keeps the project ID, records and searchable documents (marked historical), dispatches nothing new, and lets in-flight runs finish. Restoring it returns the project exactly as it was.
- A project is an ongoing scope of responsibility and need not have a product repository. Infrastructure monitoring can be a project with server-room spaces.
- Fleet normally suggests or chooses a configured host using task needs, host capabilities, and available capacity, with a user override. The host remains visible as execution context rather than the primary navigation path.
- Spaces are views over shared records: a task or report can appear in several spaces without being copied. Agents may create draft spaces at runtime; retained definitions are versioned in the project's management repository.
- Spatial and visual presentation is the default design bias: position, colour, motion, and compact visual summaries should carry as much of the state as they can usefully express. Lists, charts, search, and the document reader remain appropriate for dense or detailed information.
- Each space suggests a presentation, and the user can switch and retain a per-space preference.
- The workspace supports answering questions, accepting proposed changes, resuming work, and dispatching agents, with the underlying change or command available for review. Routine work should not require the user to administer Git branches or approval steps.
- A role such as librarian remains visible when idle; individual agent runs appear only while active. The persistent visual object is the role or workarea, not an imaginary continuous process.
- An idle role has a recognisable station or card showing its remit, queue, last outcome, and next trigger or scheduled work. An agent appears at the station only during an actual run.

## Restoke examples

- V2 overhaul is an epic. Supplier migration and development experience are workstreams; findings from one may feed another. Supplier slices are milestones, and an active slice may have a Ralph loop with many steps and parallel agent runs.
- The active slice workarea shows a plan wall, executive briefing, task reports, and questions needing attention. Completion requires explicit criteria and evidence. Once accepted, its dossier preserves the outcome and selected evidence; an archived view pins the records and space-definition revision used at closure.
- Invoice analysis is an epic currently waiting for more data. Its runtime-defined lab can display corpora, observations, error findings, strategies, and experiments without Fleet knowing invoice-specific column or row roles.
- Server monitoring is an ongoing infrastructure project. Its spaces show resources, observations, findings, and attention items; stale readings must not appear as current health.

## Identity, storage, and sync

- Projects have stable IDs independent of name, repository, and host. An existing ID permits a configured host to link automatically; matching repository information can suggest a link, while matching names alone cannot.
- Each project has one logical authoritative home. A per-project Git management repository holds durable plans, summaries, decisions, dossiers, and retained space definitions. Product documents stay in a product repository when they belong there. Each document or plan has one canonical location, including when an external tracker is used.
- Each project has a library for its produced documentation and assets, including PRDs, plans, task summaries, decisions, agent traces, images, and datasets. Entries identify their source, related work, canonical location, availability, and current or historical status. The library may link to external resources such as Google Drive documents without copying them or treating access as ownership. Cross-project search includes available product repos, management repos, retained reports, and indexed external references.
- Live status, metric samples, raw traces, and pending synchronization data stay outside Git. Server-local `.fleet` records hold memberships, run data, and copies of project state rather than independent accepted versions.
- A worker can continue while the project home is unavailable, recording runs, reports, and proposed edits. Its shared state is marked stale. On reconnection, independent edits may merge; conflicting claims remain proposals for review and are never overwritten by arrival order.
- Writes to the management repository's main branch are serialized behind the scenes. Owning roles may commit routine, evidenced progress, while changes requiring user acceptance remain proposed revisions; each change retains author and source-run provenance. The user should not need to manage the Git workflow directly.
- State changes to work items, attention items and runs are timestamped and kept for at least a week, independently of raw traces, so change markers can be rebuilt honestly. A catch-up replay may use this history later; it is not required for the first release.
- Raw traces remain on workers under a retention policy. The project home retains final reports, decisions, and evidence cited by durable dossiers. Links to unavailable traces remain visibly unavailable.

## Authorship and attention

- Orchestrators act as the user's proxy for their scope of work. They maintain progress and working summaries, publish routine updates at meaningful outcomes or handoffs, and accept completion when criteria are evidenced and their mandate permits it. They make and record decisions within their authority, grouping or resolving routine questions so the user sees only decisions that need their judgement.
- A work item at any level may have a versioned mandate stating its goal, constraints, completion criteria, decision authority, and escalation conditions. Orchestrators may refine working plans within that mandate. Changes to goals or priorities, disputed criteria, and decisions beyond delegated authority go to the user, informed by risk and cost.
- The librarian is a persistent role invoked when there is documentation work. It may update indexes and make routine, reversible documentation improvements directly in Git; changes to goals, accepted decisions, or disputed facts require user review.
- Project vocabulary may evolve during work. Agents may add terms and fields in drafts; retained definitions are versioned, and changes that reinterpret existing records require review and a migration plan.
- Only explicit decision requests, genuine blockers, and actionable alerts interrupt the user. Attention items have an owner, source, and open, acknowledged or snoozed, and resolved states; reading one does not resolve it.
- Fleet represents the decisions agents bring to the user, using mandate, risk, and cost as context. Policies for particular external actions, such as metered API calls, are outside this workspace design.
- Waiting work names a resume condition and becomes ready for review when that condition is met. On-hold work stays put until the user resumes it. Neither state automatically starts an agent.
- A waiting epic that becomes ready stays where it is, in background or on hold, without interrupting the user, unless the user opted into that condition. The same applies to a shuttered project whose condition is met. Genuine urgent alerts still use the attention system.
- Observations carry a source, timestamp, and freshness period. When fresh readings stop, current health becomes unknown or stale even if the last value was healthy.

## Deferred and open

- Host authorization and access policy are deferred in favour of simple configured-host onboarding for now. Project ID is an identity, not an access grant. Single-user operation comes first; small-team support is a later extension.
- Detailed interaction rules for actions and default spatial layouts remain for the detailed-design phase.
- The first detailed design should follow the active Restoke supplier slice end to end. Invoice analysis and server monitoring are validation cases for the shared model; their specialized spaces can follow later.
