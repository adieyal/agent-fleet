# Ralph loop: V2 suppliers, slice 4 (supplier editing), parallel

Runs on the home server (`ssh home`); this machine holds the source copy of the
loop files and starts it. Stories US-058..US-072 (prd.json) run up to 3 at a
time, each in `worktrees/v2-slice4-<id>` on `ralph/v2-suppliers-slice4-<id>`,
in `dependsOn` order. Passed stories are merged into `ralph/v2-suppliers-slice4`
(integration worktree `worktrees/v2-slice4`, based on
`ralph/v2-suppliers-slice4-base` = adi/v2-suppliers 6ff66e20a). Nothing is
pushed or merged into adi/v2-suppliers.

## Start (from this machine)

    /home/adi/Development/restoke/webapp/ralph/v2-suppliers-slice4/start-on-home.sh

