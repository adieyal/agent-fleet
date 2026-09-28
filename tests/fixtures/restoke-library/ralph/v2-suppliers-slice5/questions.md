# Slice 5 (bulk actions, selection, merge, copy): decisions
**Answered:** all 30 (2026-09-26), see corrections.md; follow-ups in follow-ups.md. Against the recommendation: Q9 (a) copy legacy; Q13 (b) say "read only".
## Scope and shape
1. **Folding in slice-4 US-071.** Its 8 commits on home/ralph/v2-suppliers-slice4-US-071 were gated against the slice-4 tip, not against adi/v2-suppliers. The last two were never reviewed. None of the
2. **Flag.** Ship slice 5 under V2_SUPPLIERS with no flag of its own, as slice 4 did.
3. **Story order.** The order is:
4. **Step-zero PR to master: new holes.** Slice 4 Q4 already sends copy_supplier_items_to_other_venues' unscoped targets (supplier_views.py:2828-2851) to a separate PR on master. Slice 5 found more:
5. **Dialogs in the URL.** Legacy's slice-5 modals do not survive a reload, and none has a deep link (grep of templates for `modal=`: only newSupplierModal).
6. **Merge items: reuse the Lit modal or rewrite it.** Legacy's merge is a Lit element, `<supplier-item-merge-modal>` (V2 frontend/src/features/supplier-item-merge/, with merge-field-grid and merge-re
## Permissions (server side)
7. **Rules per endpoint (menu rule).**
8. **Bulk delete of read-only suppliers.** Under Q7 a non-admin can bulk-delete read-only suppliers, which their own per-row Delete hides from them (supplier_actions_dropdown.html:33,59). Slice 4's si
9. **Merge suppliers: which suppliers can be absorbed.** The options are the restaurant's other suppliers, read-only and internal ones included (supplier_views.py:2228-2233). So a non-admin can soft-d
## Behaviour where legacy looks buggy, or conflicts with a V2 rule
### Supplier list selection (US-076)
10. **Where selection exists.** Checkboxes, select-all and Bulk Actions exist only in list view (view_utils.py:110-115) and at md and above (`d-none d-md-inline-block`, supplier_listing_row.html:6; su
11. **Select-all and search.**
12. **"These 0 suppliers".** Legacy's modals show only a count (`.supplier-count`, suppliers.html:138-140,161-163), with no zero guard, so the stale select-all can open "delete these 0 suppliers" and 
13. **Read-only wording shows raw tokens.** The header, body and button print `data-state` as is: "Bulk Mark Suppliers as readonly", "…mark these 3 suppliers as readonly?", "Yes, Mark as readonly" / "
14. **Row checkbox markup.** Legacy's row checkbox is a bare `<input type=checkbox name=supplier class="mr-3 …">` with no label (supplier_listing_row.html:6). V2 lint runs jsx-a11y (skill § Enforced),
### Every slice-5 dialog
15. **Pending, failure and closing.** None of the legacy modals has a pending state or a failure handler.
16. **Success toast.** On success, bulk create shows `show_success(N + " items created successfully")` and bulk conversion shows `show_success("Conversions added successfully")` (base.js:6582-6583, 66
17. **Refresh after success.** Legacy calls reload_current() after bulk delete, bulk read-only, merge suppliers, bulk edit and copy. Bulk create and conversion do not reload. V2 refreshes the affected
### Merge suppliers (US-079, US-080)
18. **Legacy's modal shape.** The modal (merge_supplier_modal.html) has:
19. **What merge moves.** `Supplier.merge_supplier` (models.py:4185-4193) makes four queryset updates:
### Item bulk actions (US-081..088)
20. **Names in the item dialogs.** Bulk edit, create and conversion list every selected item's name joined by ", " (base.js:6552,6604,6641), with no count and no cap. Slice 3's bulk delete/deactivate 
21. **Stale selection in legacy's item modals.** The Bulk Actions entries enable on any checked box on the page, including the inactive card and select-all (base.js:6746). The modals read only the act
22. **Bulk edit semantics.**
    - Answer 400 for a choice or UOM outside the options (the Q7 precedent: bad input is 400, shown as Oops).
23. **Bulk edit dialog look.** It is a `modal-lg` with ten field toggles, each revealing a card that is hidden by default and slides down (base.js:6664-6676). Unticking a toggle clears its field, and 
24. **Bulk create labels.** The menu says "Bulk Create Products" when `restaurant.isCentralKitchen`, otherwise "Bulk Create Recipes" (supplier.html:135-137). The modal title and body say "Products"/"D
25. **Bulk create failure modes.**
26. **Bulk conversion faults.**
27. **Merge items behaviour inherited from the Lit modal (if Q6 is (a) or (b)).**
28. **Bulk copy to other venues.**
## Audit events (Q14 applies: exact legacy names and properties, sent only on success)
29. **The slice-5 events.** Every one is sent before its view in legacy (decorators.py:474-477), and "Restaurant ID" is the user's own restaurant (decorators.py:433).
## Visual compares
30. **States legacy cannot show.** The standing rule is to confirm that legacy can show a state before adding it (corrections.md Milestone 1). Legacy cannot show:
## Loop questions
  Answer (supervisor, applying Q30, 2026-09-26): accepted; see corrections.md [US-081].
  Answer (human, 2026-09-26): accepted; see corrections.md [US-087] Tenancy probe requesters get a second venue.
