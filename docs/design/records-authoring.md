# Management records

Register an existing, clean local Git working tree with
`fleet project management PROJECT-ID /absolute/path`. Workspace owns the
registration. Repeating the command resumes the project's summary cutover.
A registered repository cannot be reassigned by this command.

`fleet summary set` keeps its existing arguments. New summaries require a
management repository; existing store summaries remain readable until that
project's registration migrates them. Cutover commits each summary as
`summaries/WORK-ID.json`, then removes its store body and replaces legacy
summary history bodies with cutover markers. Status reads the confirmed Git
revision, so edits in the working tree cannot silently change accepted summaries.

Records' `write` facade takes a project, relative path, body, idempotency key,
actor and optional source run. It records metadata-only intent, commits under
a process lock in the repository's Git directory, and confirms the revision.
Reusing a key with different content or provenance is rejected. A failed write
returns its recorded error and no revision. `reconcile()` inspects pending
intents; the next write also reconciles the project's pending intents before
committing. Unchanged reconciliation creates no history.

Mandates are JSON objects with `goal` (a nonempty string), `constraints`,
`decision_authority`, `escalation_conditions`, and `criteria_it_may_judge`
(arrays of nonempty strings, possibly empty). Use `write_mandate` to validate
before authoring and `mandate` to parse the confirmed version. These records
describe authority; enforcing activation authority belongs to its own story.

The writer refuses uncommitted changes. If interrupted while staging a file,
reconciliation records a failure when it finds no matching commit; the working
tree must be inspected and cleaned before submitting a new write key.
