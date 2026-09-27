# Work commands

Work owns work items, relations, criteria and working summaries. Its public
contract is `fleet.modules.work.WorkFacade`; `open_work` supplies SQLite and
local evidence adapters. Read consumers use `get`, `list`, `criteria`,
`relations`, `summary` and `kinds`, without reading Work tables.

Each command records the previous and new record as JSON in state history,
including its actor. Blocking and unblocking use the Attention facade bound
to the same SQLite unit of work. Reblocking explicitly reopens the existing
source/reference item. Other attention sources retain their existing behavior.

`fleet work add TITLE --project PROJECT --goal GOAL --actor ACTOR` creates a
task. `--kind` selects epic, workstream, milestone, task or a project-added
label; using a new label adds it to that project's kinds. `--parent` supports
any nesting depth. `fleet work move ID --parent ID` changes the parent;
`--root` removes it. Both require `--actor`.

`fleet work set ID --condition waiting --resume-condition TEXT --actor ACTOR`
records waiting. Only an explicit command changes the condition;
`fleet work ready ID --actor ACTOR` requires waiting and sets ready for review.
`set` also accepts title, goal, kind, next-step and focus. Focus is priority or
background and only applies to epics. `fleet work relate FROM TO --actor ACTOR`
records a depends-on relation.

`fleet criterion add WORK_ID TEXT --verification checked --evidence-reference
/absolute/file --required-result passed --actor ACTOR` names evidence. The
local adapter verifies the file exists; an optional required result matches
the `result` field of a JSON file. It does not execute tests. A missing result
stays unknown and cannot satisfy a specified result. Other evidence sources
can implement the evidence reader port when their owning modules arrive.
`fleet criterion meet ID --evidence /absolute/file --actor ACTOR` verifies and
records acceptance; evidence flags are repeatable. Judged and accepted criteria
require an actor but do not require a checked evidence specification.

`fleet summary set WORK_ID --purpose TEXT --done TEXT --doing TEXT --next TEXT
--authoring-role ROLE --actor ACTOR` stores a working summary. All commands
return JSON records. Summary bodies remain store-owned until the planned
Records cutover. No run completion or time passage changes work or criteria.
