# Fleet workspace language

Fleet follows ongoing work across hosts while individual agent processes come and go. These terms describe the persistent work and the temporary activity that contributes to it.

## Language

**Project**:
An ongoing scope of responsibility with an identity independent of its name, repository, or host. A project may span several hosts and repositories, or have no product repository at all.

**Focus**:
The user's explicit choice of where to put resources: **priority** or **background**. Focus applies to projects and, within a project, to epics. Activity never changes focus. Focus is not navigation; moving between scopes is *entering* them.
_Avoid_: Focus to mean the scope being viewed; parked as a focus state

**Project capacity**:
The fixed number of projects that can be live at once, shown as floors of the building: six by default, ten at most. Raising it is a deliberate setting, not a side effect of starting work.

**Shuttered project**:
A project archived out of the live workspace to free capacity. It keeps its identity, records and searchable documents, accepts no new dispatches, lets in-flight runs finish, and can be restored exactly.
_Avoid_: Parked project

**Epic**:
A substantial goal within an ongoing project that can contain several workstreams.

**Work item**:
A persistent piece or scope of work within a project, optionally nested inside another work item. Its type and relationships help present it without fixing the work hierarchy to a set depth.

**Workstream**:
An independently progressing line of work within a project, often under an epic. A workstream may produce findings or improvements that help other workstreams.

**Milestone**:
A bounded checkpoint within work with explicit completion criteria. A slice is a milestone and may involve nested work, many tasks, and agent runs.
_Avoid_: Slice as a separate epic or workstream

**Waiting**:
The state of work that names a resume condition. When the condition is met the work becomes ready for review; no agent starts automatically.

**On hold**:
The state of work set aside by the user inside a live project. It stays put until the user resumes it and does not wake because of activity elsewhere.
_Avoid_: Parked, for work inside a project

**Agent run**:
One bounded execution of an agent process or session. It contributes to persistent work but does not itself represent that work.

**Role**:
A persistent project responsibility, such as orchestrator or librarian, fulfilled by one or more temporary agent runs. It remains visible and meaningful while no agent is running.

**Mandate**:
A versioned statement of a role's goal, constraints, completion criteria, allowed decisions, and escalation conditions for a scope of work. It guides decisions according to risk and cost.

**Run trace**:
The detailed activity emitted by one agent run. It is held by the worker under a retention policy and is not itself the durable project record.

**Dossier**:
A durable account of completed work, such as a slice, that preserves its outcome, decisions, and selected supporting evidence after the agent runs end.

**Project home**:
The logical authority for a project's identity, accepted workspace state, and links to canonical records. It combines a management repository for durable records with separate live state; server-local copies do not independently define accepted project state.

**Management repository**:
A Git repository for one project's durable plans it owns, summaries, decisions, dossiers, and space definitions. It is separate from any product repository and from raw run traces.

**Space**:
A view over shared project records, arranged for a particular purpose such as following an active slice or monitoring a server. Spaces may overlap without owning separate copies of the records they show, and may begin as drafts before being retained.

**Attention item**:
A decision request, blocker, or actionable alert that calls for a person's attention. Ordinary agent activity and new reports do not become attention items merely by arriving.

**Observation**:
A timestamped reading or state about a subject, with a source and a period in which it can be treated as current. An older observation remains part of history but does not establish present health.

**Project library**:
The project's orderly index of canonical documents and supporting artifacts, including their source and whether they are current or historical.

**Executive summary**:
A concise account of a project's or work item's purpose, completed outcomes, current work, and remaining work. It identifies its authoring role and update time without requiring claim-by-claim citations.
