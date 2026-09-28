# Ralph loop: V2 suppliers, slice 6 (imports, add or update from file): DRAFT

**Status.** prd.json is drafted and has not been reviewed. questions.md has **43 open questions** (Q1-Q43, none answered). The PRD follows their recommendations, and every criterion that depends on one cites it as (Qn). The loop files (ralph.sh, prompt.md, gates.sh …) are not copied from slice 5 yet, so nothing here runs.

**Base.** adi/v2-suppliers at 39afc88cc (worktrees/v2-supplier-entity), which already holds slice 5. The slice branch is ralph/v2-suppliers-slice6, with the integration worktree at worktrees/v2-slice6. Nothing is pushed. Slice 6 ships under V2_SUPPLIERS.

## Scope

Every 'Slice 6 —' and 'Until slice 6' entry in the V2 parity checklists (legacy facts: notes/legacy-map.md):
- **Suppliers list:**
  - Import suppliers from the accounting system (Xero, QuickBooks).
  - Import suppliers from the Gmail inbox.
  - The OAuth returns and the legacy `?modal=` deep links, which then land on the V2 dialogs.
- **Supplier page:** Add or update items from a file (CSV/XLSX with column mapping, Upload Invoice, Add items manually). It opens from the toolbar, and automatically for an empty basket. This replaces the interim link to the legacy supplier page.
