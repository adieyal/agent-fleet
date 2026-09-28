# Ralph loop: V2 suppliers, foundations and slice 1

Run: `./ralph.sh [max_iterations]` (default 40). Worktree `worktrees/v2-ralph`,
branch `ralph/v2-suppliers-slice1` off `adi/v2-tanstack`. Nothing is pushed.

| File | Role |
| --- | --- |
| `prd.json` | Objectives O1–O6, out-of-scope list, 26 stories. The loop sets `passes`, `attempts`, `blocked`. |
| `prompt.md` | Implementer: one story, simplicity rules, log edge cases instead of handling them |
| `gates.sh` | Referee gates, outside the repo so agents cannot weaken them |
| `evaluate.md` | Read-only evaluator per story: criteria, tests, simplicity, objective |
| `milestone.md` | Every 6 passes: objective status and course corrections (`corrections.md`) |
| `report.md` | Final report agent, writes `REPORT.md` |
| `edge-cases.md`, `questions.md`, `cost.csv`, `progress.txt`, `logs/` | Loop output |
