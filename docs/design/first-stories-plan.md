# First stories: detailed plan

**Status:** Working plan for the first-stories Ralph loop. It details the phases of [the first working workspace plan](first-working-workspace-plan.md); ownership follows [ADR 0008](../adr/0008-business-ownership-boundaries.md) and [the directory structure](business-module-directory-structure.md).

These follow the order from before. Each story ends with something working that you can try on the real supplier slice, and each one's checks double as its acceptance tests. The file references come from the plan's implementation map. New modules and command names are suggestions.

## Story 0: Groundwork (prerequisite, not a story)

Story 1 needs somewhere durable to live, so part of Phase 0 must come first. Build the smallest version of it:

- a SQLite store with migrations and transactional commands, used by both `fleet/cli.py` and `fleet/web/server.py`;
- a state-change history table (subject, from, to, actor, time), kept for at least a week, which every later story writes to;
- the recorded-document path restriction.

The path restriction matters because story 2's reader will open recorded documents, and the traversal and symlink hole should be closed before more documents flow through it.

Migrating projects, floors and focus out of `config.json` and `workspace.json` can happen alongside stories 1–3. It doesn't block them, as long as attention moves first.

**Checks:** a failed transaction changes nothing; the CLI and web app can't lose each other's writes; an arbitrary recorded path can't be read through the dashboard.

## Story 1: What needs me?

**Outcome:** a lantern on a floor tells me something needs me, from any level, and it stays lit until I deal with it.

**Build**

The model:
- `attention_item`: project, optional work item and run, kind (decision, blocker, alert), owner, source, a source reference, a headline of 12 words or fewer, a context reference, state (open, acknowledged, snoozed, resolved), a snooze-until time, and resolution details.
- A uniqueness constraint on (source, source reference), so a repeated signal updates one item instead of creating many. That rule is what keeps the lantern trustworthy.

The plan says attention actions already live in `workspace.json`. Migrate those into this table rather than starting over.

Sources:
- **Manual:** `fleet attention add`, for testing and for things you want to track.
- **Run waiting for input:** this is the one that matters, and it needs a spike first.
  - Claude Code's hooks include a notification event that fires when a session needs permission or is waiting for input, and a stop event.
  - Codex has a `notify` hook; check whether it covers the same case.
  - fleetd would turn these into an observation, and the controller would turn the observation into an attention item with the run's host and job as its source reference.
  - When the session resumes, the source resolves the item with the resolution "answered in session".

Presentation:
- On the existing deck, one lantern per floor, with a count when there is more than one item. It swings once on arrival, then glows steadily.
- If the lift panel exists yet, the same glyph goes on the floor's lift button.
- A plain list at the front desk. Selecting an item opens its context in the reader.
- Reading an item changes nothing.
- CLI: `fleet attention list | ack | snooze | resolve`.

**Checks**
- Restarting Fleet keeps every open item.
- Opening an item doesn't acknowledge it.
- A snoozed item returns at its time.
- Ten identical waiting-for-input signals produce one item.
- If an item's host goes quiet, the item stays open and its context shows "last seen".
- The lantern is identifiable in greyscale and with reduced motion.
- Activity on a windowed floor with no attention produces no lantern.

**Leave out:** answering (story 4), blockers raised from work items (story 2), anything raised by an orchestrator (story 7).

**Unknowns:** how reliably each runtime reports "waiting for input". If only Claude Code does, ship with that and give Codex runs manual items.

## Story 2: Where does this slice stand?

**Outcome:** with no agents running and both hosts offline, I can open the supplier slice and see its goal, how close it is to done, what's next, and what's open.

**Build**

The model:
- `work_item`: project, optional parent, kind label (epic, workstream, milestone and task as defaults; projects may add their own), title, goal, condition (none, waiting with a resume condition, ready for review, blocked, on hold, complete), next step, focus (epics only), and timestamps.
- `relation`: from, to and type, starting with depends-on.
- `criterion`:
  - work item, text, and verification kind (checked, judged, accepted);
  - for checked criteria, an evidence specification naming what must exist;
  - state, who met it, evidence references, and when.
  - The store refuses to mark a checked criterion met without evidence, or any criterion met without an actor.
- `summary`: purpose, done, doing, next, authoring role, and update time.

Keep summaries in the store for now. Story 7 moves authored summaries to the management repository, where they belong under ADR 0002. Until then the store owns them outright, so there is no second copy.

Progress is a read projection, not a stored field:
- children milestones completed out of planned, if the item has milestones;
- otherwise criteria met out of total;
- otherwise unknown.

Keep projections in their own module, separate from mutations, as the plan asks.

Authoring:
- No orchestrator exists yet, so you author the items.
- CLI: `fleet work add | set | move | relate`, `fleet criterion add | meet`, `fleet summary set`.
- Setting a work item to blocked raises a blocker attention item through story 1's model.
- Waiting-to-ready is manual for now (`fleet work ready`), because evaluating resume conditions automatically needs observation sources.
- Write a seed script for the real slice: the V2 overhaul epic, the supplier migration and development-experience workstreams, the active slice as a milestone with tasks and criteria, one earlier completed slice, and invoice analysis waiting for more data.

Presentation:
- A plain read view in `server.py` and `fleet status <project>`.
- It shows the work tree, with each item's progress mark (or "unknown"), condition, next step, criteria and their verification kinds, linked attention items, and summary.
- Keep it within the PRD's text budgets. This view is scaffolding for story 5, not the product.

**Checks**
- The Phase 1 gate passes: with both hosts offline after a restart, the project still shows its goal, progress, next step and open question.
- No percentage appears without a known total, and "unknown" never renders as zero.
- A checked criterion can't be met without evidence.
- Nesting a task four levels deep, or attaching work directly to the epic, breaks nothing.
- Blocking an item lights the lantern; unblocking it resolves the lantern.

**Leave out:** the library, runs, automatic resume conditions, and the management repository.

## Story 3: What failed, and does it matter?

**Outcome:** I can see which runs served this slice, which failed or went quiet, and what they produced. None of that silently changes the slice's state.

**Build**

The model:
- `run`: action, host, remote job ID, runtime, status (running, succeeded, failed, stopped, unknown outcome), reason, start and end times, and last-observed time.
- ADR 0007 hangs runs off actions, so linking an existing job creates an action with source "linked". That keeps the model uniform before story 6 exists.
- `library_entry`: project, optional work item and run, kind, title, source, canonical location, availability (available, unavailable, external), and current or historical.

Linking:
- `fleet run link <host> <job> <work-item>`.
- A `--work-item` flag on today's `fleet send`, so new jobs are tagged when they're dispatched. It's a small change and is replaced properly in story 6.

Observations:
- fleetd's stream updates run status.
- When a host stops reporting beyond its freshness period, the run becomes unknown outcome, never failed.
- When the host returns, reconcile by job ID.
- If fleetd can already tell that a job's process is gone without a result, record the run as failed with reason *lost* here. Otherwise that detection lands in story 6.

Outputs:
- A run's reports and trace become library entries.
- A trace pruned on the worker stays listed as unavailable.
- Add `fleet library link <url>` for external references, such as a Drive document. The link grants no access.

Presentation:
- Each work item in the read view gains a runs section and a library section.
- A failed run appears as evidence under the item, and the item's condition is unchanged.
- If a failed run has no later next step, show a quiet "no follow-up yet" marker on the item. It is not an attention item. It nudges toward the plan's rule that every outcome leaves a next step, without adding noise.

**Checks**
- Taking a host offline mid-run shows unknown, not failed. Bringing it back reconciles to the true outcome.
- A failed run leaves its work item's condition and progress untouched.
- A restart keeps every run link and library entry.
- The unavailable-trace case renders as unavailable.
- Two runs on different hosts can serve the same milestone and both appear.

**Leave out:** dispatching through actions, claims, and usage capture (all story 6).

## Story 4: Answer and unblock

**Outcome:** I follow a lantern, read the question with its context, answer it, and the work continues. After this story Fleet saves me time every day, which makes it the dogfooding checkpoint.

**Build**

The model:
- `decision`: attention item, question, answer, actor, context, affected work items, and time.
- Resolving is one transaction: record the decision, resolve the attention item, update the affected work item (clearing blocked, and optionally setting the next step), and write the state history.

Delivery:
- A question from a live session needs the answer delivered back to that session. Track delivery separately from resolution:
  - the attention item resolves when the decision is recorded;
  - a `delivery` record tracks getting the answer to the run, with an idempotency key.
- If delivery fails or the host is offline, the delivery retries on reconnect, and a lasting failure raises an alert on the run.
- This keeps attention's four states clean, and it means answering while a host is offline doesn't lose the answer.
- How to deliver depends on how fleetd hosts sessions: writing to the session's terminal, or resuming headlessly with the answer as the next prompt (both runtimes can resume a session by ID). Spike this alongside story 1's hook work, because they touch the same code.

Presentation:
- Lantern → reader panel with the question, its context, any proposed change or command shown for review, and an answer box.
- If the question offers options, show them as choices.
- CLI: `fleet answer <id> "..."`.
- Optionally, the asking agent perks up and gets back to work.

**Checks**
- Answering resolves exactly one item and records the actor.
- The session receives the answer exactly once, including when the first delivery attempt's response is lost.
- Answering while the host is offline records the decision immediately and delivers it on reconnect, without duplicating it.
- A blocked item unblocks.
- The decision appears in the read view under the work it affected.

**Checkpoint:** before starting story 5, run the real slice on stories 1–4 for a week. Note every time you still had to read a trace to find out what needed you, and turn each one into a fix or a new source.

## Story 5: The slice's bench

**Outcome:** the supplier slice's workarea, in the deck, passes the five-second glance and redacted-text tests.

**Build**

Reaching the bench:
- Build the minimum L1/L2 needed to get there, following the decisions log: a cluster of benches stands for an epic's room, and walls are optional.
- Enter from the floor, to the room, to the bench, with a breadcrumb; Esc steps out one level.

The L3 bench:
- **Plan wall:** task tiles in done, doing and next order. A tile flips when its task completes.
- **Criteria lights:** each shows its verification kind with a distinct shape or glyph, not colour alone, so a lit light shows whether Fleet, the orchestrator or the user lit it.
- **Agents:** figures in host colours with action glyphs (reading, editing, testing, waiting). More than five gather into a group figure with a count.
- **Question desk:** holds the lantern when the slice has attention items.
- **Report tray:** fed from the library.
- **Briefing board:** the summary, opened on demand.

All of it reads from one state projection endpoint built on the story 2 and 3 projections. The deck never reads host streams directly for anything shown here.

Supporting work:
- Add the `?redact` debug mode and the automated text-budget counter now. Every later visual story reuses them.
- Timebox a first pass and run the five-second test before polishing anything. This story carries the most design risk, and the test is the fastest way to find out whether the bench reads.

**Checks**
- **Five-second test at the bench:** the next task, how close the slice is to done, and whether anything is blocked.
- **Redacted-text test:** the same answers with all text blocked out.
- **Greyscale, colour-vision simulation and reduced motion:** every state stays distinguishable.
- **Text budgets:** automated, and passing.
- **Wayfinding:** from the building to the bench and back without instruction.

**Leave out:** return markers, completion moments beyond the tile flip, other spaces, and dispatch from the scene.

This story can start once story 2's projection exists, running in parallel with stories 3 and 4.

## Story 6: Dispatch without duplicates

**Outcome:** I dispatch an action for a work item to a host I choose and get exactly one traceable run, whatever the network does.

**Build**

This is ADR 0007 as written, plus the patch.

Actions and claims:
- `action` gains a dispatch reason, actor, and a payload fingerprint.
- A `claim` table has a uniqueness constraint on active claims per action.
- Dispatch is one transaction: select or create the action by idempotency key, claim it, and record the intended run with a controller-generated run ID.
- The claim is released only when the run reaches a known end.

fleetd:
- Create and start become idempotent by run ID.
- A repeated run ID with a different payload fingerprint is refused.
- After any uncertain response, the controller reconciles by run ID and never re-sends blindly.
- Detect *lost* runs by confirming the process is gone with no result recorded.
- Keep the single-file host install.

Runtimes and usage:
- Keep one internal seam where Claude Code and Codex genuinely differ: invocation, result parsing, usage, and the input delivery from story 4. Don't make it a public interface.
- Record tokens and usage on the run when the runtime reports them, with their source; otherwise record an explicit unknown.

Interface:
- `fleet dispatch <work-item> --host <h> --runtime <r> "<instruction>"`.
- `fleet send` becomes a thin wrapper over it.
- A dispatch button on the bench is optional; dropping a task onto the bench can wait.
- Manual retry is allowed only after a known end, or after an explicit `fleet run resolve-unknown`.

**Checks**
- The Phase 2 gate: two concurrent claims on one action yield one run; two actions on one epic run on different hosts; retrying after a dropped SSH reply starts no second job; a disconnect marks nothing complete or failed.
- Killing the agent process on a reachable host yields a failed (*lost*) run and a released claim.
- Fault injection at each crash point: after the claim, after the remote create, after the start, and with the controller killed mid-transaction.

## Story 7: Let the orchestrator handle the routine

**Outcome:** the slice progresses through an orchestrator that records routine decisions itself. I'm interrupted only for decisions its mandate reserves for me.

**Build**

Mandate:
- It is an authored record, so its canonical copy lives in the management repository as a small structured file: goal, constraints, decision authority, escalation conditions, and which criteria it may judge.
- The controller records the commit it used as the mandate version, so there is one owner per fact.
- This story registers the management repository and moves authored summaries there. The store keeps the path and current revision.

Activation:
- `fleet orchestrate <work-item>` is the only trigger.
- It resolves the mandate version, starts an orchestrator run, and gives that run a command set against the controller: read the state projection, update progress and next step, meet judged criteria, raise attention items, dispatch actions, and propose changes.
- Every write carries the activation ID, and the controller checks it against the mandate. An out-of-authority write becomes a proposal and an attention item, never a silent refusal and never a silent success.

Where the orchestrator runs:
- For the first version, run it on the controller's machine.
- The SQLite controller is local, and ADR 0007 doesn't let remote workers write shared state, so a remote orchestrator would need an API you don't have yet.
- The orchestrator's dispatched runs still go to any host.

Records:
- Accepted summaries and decisions are committed to the management repository with source-run provenance.
- Commits are serialised behind the scenes.

**Checks**
- The Phase 3 gate: a reviewer answers the five questions from persisted state; routine decisions are recorded without prompting; an attempt to accept a user-reserved or unevidenced checked criterion is refused and reaches me; a successful run can't close work without the required evidence and authority.
- Count interruptions per slice against the manual baseline from the story 4 checkpoint. That number is the measure of whether this story worked.

## Sequencing and open questions

The critical path runs 0 → 1 → 2 → 3 → 4, with the checkpoint after story 4. Story 5 can start once story 2's projection exists. Story 6 needs story 3's run model. Story 7 needs stories 2, 4 and 6. The hook-detection and input-delivery spikes should run first, alongside story 0, because they decide whether story 1's most valuable source is feasible.

Four questions to settle as you go:
- **Input detection:** how reliably each runtime signals "waiting for input".
- **Input delivery:** how answers reach a live session, which depends on how fleetd hosts sessions today.
- **Resume conditions:** whether waiting-to-ready stays manual until real observation sources exist.
- **Orchestrator location:** whether a local-only orchestrator is acceptable for the first release.
