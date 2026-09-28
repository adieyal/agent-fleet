# V2 review findings — adi/v2-suppliers @ 39afc88cc

Worktree `worktrees/v2-supplier-entity`. Frontend paths are relative to `frontend/src/v2` unless they
start with `frontend/`. Backend paths are relative to `restoke/`. Severity: **H** high, **M** medium,
**L** low. Companion file: `architecture-map.md`.

Method: read-only static review (grep, `wc`, reads) by three sweeps, with the high-severity backend
claims spot-checked by hand. **Not measured:** the sandbox denied node, python and git, so there are no
gate timings, no `v2:new --dry-run` line counts and no branch-merge checks. These are marked where they
matter.

## 1. Consistency and completeness
