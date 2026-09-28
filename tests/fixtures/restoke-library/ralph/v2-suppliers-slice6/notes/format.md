# Slice-5 PRD format (to copy for slice 6)

Source: ralph/v2-suppliers-slice5/prd.json, questions.md, corrections.md, follow-ups.md, REPORT.md, notes/prd-summary.md, and slice 5's own notes/format.md (the slice-4 format, which slice 5 followed with the changes below). Base for slice 6: adi/v2-suppliers at 39afc88cc (worktrees/v2-supplier-entity), which already holds slice 5 (supplier-editor and supplier-items parity.yaml are `ready: true`).

## Top level

Keys, in this order: `project`, `status`, `branchName`, `baseBranch`, `worktree`, `plan` (null), `description`, `objectives`, `outOfScope`, `userStories`, `decisions`, `openQuestions`.

- `project`: "Restoke V2 suppliers migration: slice 6, <topic>".
- `status` in a draft: "DRAFT — not reviewed by the human; open questions in questions.md (Q1-Qn, none answered); criteria that depend on one cite it as (Qn)". Slice 5 cites the question inline as "(Qn)", not "per questions.md".
- `branchName` ralph/v2-suppliers-slice6; `baseBranch` adi/v2-suppliers; `worktree` /home/adi/Development/restoke/webapp/worktrees/v2-slice6. The loop runs each story in worktrees/v2-slice6-<id> on ralph/v2-suppliers-slice6-<id> and merges into the integration branch, based on ralph/v2-suppliers-slice6-base = adi/v2-suppliers 39afc88cc (README.md).
- `description`: one paragraph. It lists every legacy action the slice moves and where it lives (/app/suppliers, /app/suppliers/<id>). It says what V2 does instead of today's LegacyLink or legacy href, that it ships under V2_SUPPLIERS, and which endpoints it adds.
