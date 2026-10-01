# Epic charter: Positional transcriber, Liquid Mix + ALM

Fleet epic `9e51af8e`, project invoice-training. It inherits `invoice-training.constitution.md`. Draft, 2026-10-01.

## Goal

Store Liquid Mix and ALM line items correctly by reading the PDF text layer by position, using each supplier's declared profile. Anything that cannot be read cleanly falls back to today's route.

## Done means

1. The PRs are merged in order: #9467 (prep), #9465 (module), then the integration PR.
2. The acceptance invoices map end to end with 0 flags, and every value matches the print, descriptions included:
   - Liquid Mix 4940634 (`1c2d379c…pdf`)
   - ALM 30326 (`44617c03…pdf`)
3. Corpus results: no accepted value is wrong. The acceptance-rate bar is for the user to set; until then, report the rate without treating it as a target.
4. Suppliers without a profile produce identical output on the replay set (the V1 method).
5. After deploy, production is confirmed with the flag-rate command on live invoices.

## Anti-goals for this epic

1. No LLM transcription for profiled suppliers, and no LLM fallback for scans. Both are round 2.
2. No pricing factors in checks. The ALM accounts with LUC × 1.005 fall back.
3. No parse/interpret split refactor. That is round 2.
4. No support for new suppliers or layouts (ALM DIRECTS, credit notes) in this epic.

## Canonical line

`master` ← #9467 `adi/refactor/invoice-ocr-boundaries` (`8bbdbd648`) ← #9465 `adi/feat/invoice-layout-profiles-standalone` (module) ← integration PR. The integration PR is built from:

- `71bc0dd38` on `adi/feat/invoice-analysis-profiles`
- the integration commits of `adi/feat/invoice-positional-transcriber` (`1050f1d4b`)

Superseded, not to be built on:

- #9457 (`adi/feat/invoice-layout-profiles`)
- the `ilp/*` branches on home
- `ilp-port`, `ilp/X1-alm-note` and `ilp/X2-alm-gpt41`

## Decisions in force (2026-10-01)

1. #9465 holds all module work and no integration. The poppler word-box reader ships with the module.
2. Positional profiles carry no `transcription_model`, and validation rejects one.
3. Minimum delivery fees and similar printed charges are stored as separate charge lines (other expense), not as freight.
4. Fixture `quantity` and `unit` fields are in printed form. Acceptance compares decoded values with what the print means.
5. Entry points may import the composition root. The module never imports `domain/business/invoice`, `providers` or `textract.py`.
6. The typing gate is `mypy --strict --follow-imports=silent` over the module, its `persistence` packages and poppler.
7. `ignore_unheaded_cells` stays in the module for LLM profiles. Positional profiles may set it.

## Open (user)

- The `textract.py` edits in the integration PR (126 lines added, 15 removed on `1050f1d4b`). Most are gated on profile markers, but the diff also reformats and moves existing lines on the shared path. Each needs the user's approval.
- The strict provider-response parsing, which changes behaviour on malformed output for suppliers without a profile.
- Closing #9457, and cleaning up the superseded branches.
