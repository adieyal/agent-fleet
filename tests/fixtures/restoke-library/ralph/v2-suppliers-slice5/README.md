# Ralph loop: V2 suppliers, slice 5 (bulk actions, selection, merge, copy), parallel

Runs on the home server (`ssh home`); this machine holds the source copy of the
loop files and starts it. Stories US-073..US-090 (prd.json) run up to 3 at a
time, each in `worktrees/v2-slice5-<id>` on `ralph/v2-suppliers-slice5-<id>`,
in `dependsOn` order. Passed stories are merged into `ralph/v2-suppliers-slice5`
(integration worktree `worktrees/v2-slice5`, based on
`ralph/v2-suppliers-slice5-base` = adi/v2-suppliers 438ab9fb8). Nothing is
pushed or merged into adi/v2-suppliers.

## Start (from this machine)

    /home/adi/Development/restoke/webapp/ralph/v2-suppliers-slice5/start-on-home.sh

