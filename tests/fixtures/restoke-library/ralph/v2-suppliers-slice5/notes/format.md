# Slice-4 PRD format (to copy for slice 5)

Source: ralph/v2-suppliers-slice4/prd.json (and prd.before-answers.json for the draft state).

## Top level

`project`, `status`, `branchName`, `baseBranch`, `worktree`, `plan` (null), `description`, `objectives`, `outOfScope`, `userStories`, `decisions`, `openQuestions`.

- `project`: "Restoke V2 suppliers migration: slice N, <topic>".
- `status` in a draft: "DRAFT — not reviewed by the human; open questions in questions.md". After review: "Reviewed by the human <date>: …; see corrections.md".
- `branchName` ralph/v2-suppliers-sliceN; `baseBranch` adi/v2-suppliers; `worktree` /home/adi/Development/restoke/webapp/worktrees/v2-sliceN.
- `description`: one paragraph listing every legacy action the slice moves, where it lives (/app/suppliers, /app/suppliers/<id>), what V2 does instead of the LegacyLink, the flag it ships under, and the new endpoints.
