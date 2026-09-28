Run `scripts/checks/phase3-gate.sh <milestone-id>` with the user's real Fleet
environment at the checkpoint. Automated gates run only the temporary-store
scenario test, never this operator command against live state.

The script adds a **Phase 3 gate** milestone beneath the supplied slice, commits
a separate test mandate and routine decision to its management repository, and
records scripted orchestrator commands and faked host outcomes. It leaves two
explicit proposals as user-owned attention items for review. It never contacts
a host or invokes an AI runtime. Each invocation creates a new gate milestone;
these records remain in the slice. A clean, registered management repository
and a project that permits dispatch are required.

The five answers printed at the end come from a reopened controller store.
Inspect them again with `fleet status <project>` or `fleet status <project>
--json`. Rejected writes are quiet; the scripted orchestrator explicitly
proposes the user-reserved and unevidenced acceptances. The successful run
leaves work incomplete.

`Interruptions` counts distinct user-owned attention items attached to a
milestone or its descendants, across all attention states. Answering, resolving,
snoozing, or receiving a duplicate source signal does not subtract or add an
item. Project-only items cannot be attributed to a slice and are excluded.
Nested milestones report their own subtree counts; do not sum overlapping
milestone counts. There is no M3 baseline yet, so no comparison is displayed.
