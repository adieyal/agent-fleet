# Slice 6 legacy map: imports, onboard suppliers, add or update items from a file

**Scope.** Every 'Slice 6 —', 'Until slice 6' and 'is slice 6' entry in worktrees/v2-supplier-entity (adi/v2-suppliers 39afc88cc) `frontend/src/v2/features/{suppliers,supplier-detail,supplier-items,supplier-editor}/parity.yaml`:

| Parity entry | V2 today | Section |
|---|---|---|
| suppliers/parity.yaml:121 'Slice 6 — import suppliers from the accounting system (Xero, QuickBooks)' | `LegacyLink to="suppliersModal" modal=importSuppliersFromXeroModal`: header button page-actions.tsx:28-46 and the "+ New" entry :91-98 | A |
| suppliers/parity.yaml:124 'Slice 6 — import suppliers from the Gmail inbox' | the same LegacyLink with modal=importSuppliersFromGmailModal: page-actions.tsx:48-61 and :100-106 | B |
| suppliers/parity.yaml:127 'Slice 6 — request a supplier basket (onboard suppliers by email)' | nothing; no V2 entry point | C |
| supplier-detail/parity.yaml:391 'Slice 6 — add or update items from a file, opened automatically for an empty basket' | no auto-open | D |
| supplier-detail/parity.yaml:394 'Until slice 6, Add or update from file opens the legacy supplier page' | `<a href={legacyPage}>` supplier-items-cards.tsx:502-510 | D |
| supplier-items/parity.yaml:20 and supplier-editor/parity.yaml:661 (out-of-scope comments naming slice 6) | comments only | D, A/B |
