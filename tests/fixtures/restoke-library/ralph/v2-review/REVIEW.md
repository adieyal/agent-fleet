# V2 architecture review — adi/v2-suppliers @ 39afc88cc

Focus: how easy the V2 code is for a developer or a coding agent to read and maintain, and to extend
with a new route, endpoint, field or shared component. Evidence and full lists are in
`architecture-map.md` and `findings.md` in this directory. Paths are relative to
`worktrees/v2-supplier-entity`; frontend paths are relative to `frontend/src/v2` unless they start with
`frontend/`.

## What could not be done

- **The hands-on agent-friendliness probe was not run.** It was blocked by permissions, and it will
  run separately. No worktree was created and there is no `probe.md`. The cost-of-change numbers below
