# Slice 4 (supplier editing): decisions (all answered 2026-09-25)
## Scope and shape
1. **Flag.** Ship slice 4 under enableV2SuppliersList / enableV2SupplierDetail with no flag of its own, as slice 3 did?
   **Answer:** go with the recommendation (human, 2026-09-25), with one change: the two flags become one, V2_SUPPLIERS (US-072); see corrections.md.
2. **Story order and splitting.** The order is: checklist (US-058), then menu callbacks and the missing entry (US-059), then edit details as read, dialog and save (US-060..062), New Supplier (US-063),
   **Answer:** go with the recommendation (human, 2026-09-25); stories now run up to 3 at a time in `dependsOn` order (prd.json); see corrections.md.
3. **Dialogs in the URL.** Slice 3 put the item dialog at ?item=. Legacy's supplier modals do not survive a reload, except New Supplier, which has a deep link (/?url=/suppliers/&modal=newSupplierModal
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
4. **Step-zero PR to master for legacy holes.** These were found while scoping:
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
## Permissions (server side)
5. **Rule set.** Legacy enforces almost nothing on the server. `new_supplier`, `delete_supplier` and the advanced save check only tenancy.
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
6. **"No departments set" bypass.** On a read-only supplier, the warning opens the edit modal for a non-admin, although the Actions menu hides Edit from them. Under Q5, their Save would get a 403.
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
## Behaviour where legacy looks buggy
7. **Edit save is lossy and silent.** Legacy overwrites every field that was not posted with a default: without the orders feature, sendVia becomes Email and fees become 0. It also sets createdBy to t
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
8. **New Supplier validation.** NewSupplierForm marks email, mobile and portal URL required, but legacy's `new_supplier_custom_validation` never checks them, because a number is compared to a string. 
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
9. **From Invoice.**
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
10. **Delete on the supplier page.** Legacy removes only the header card and leaves the user on a page for a deleted supplier. In the list's row view nothing disappears until a reload; only card view 
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
11. **Advanced settings save faults.**
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
12. **Other venues copy is destructive and unwarned.** Target items missing from the source are deactivated.
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
13. **Upgrade dialog Mixpanel event.** Legacy sends "Upgrade Package Modal Show" {source: "supplier_actions_dropdown"}, and V2 has no frontend Mixpanel client.
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
14. **Audit event names.** Legacy logs supplier edits as "Create New Supplier", with supplierid, before the view runs, so the event fires even on a 403 or 500. Read only logs nothing.
   **Answer:** go with the recommendation (human, 2026-09-25); see corrections.md.
## Loop questions
  **Answer ([US-063] tenancy probe):** go with the recommendation (human, 2026-09-25); see corrections.md "[US-063] Tenancy probe accepts a refused empty-body write". US-063 unblocked.
  **Answer ([US-066] customer state):** go with the recommendation (human, 2026-09-25); see corrections.md "[US-066] External-customer delete confirmation is not comparable". US-066 unblocked.
## [US-061] 2026-09-25T18:16:17+02:00 Edit Supplier Details: the load-failure state differs from legacy
## [US-069] 2026-09-25T19:21:43+02:00 The "Ready to Go Pro?" upgrade dialog cannot match legacy with --rs-* tokens
  **Answer ([US-063] tsc gate):** gate fixed (human, 2026-09-25); see corrections.md "[US-063] typecheck-full-changed skips src/v2". US-063 unblocked.
  **Answer ([US-061] load failure):** V2 behaviour with Oops wording (human, 2026-09-25); see corrections.md "[US-061] Details load failure". US-061 unblocked.
  **Answer ([US-069] tenancy probe rights):** keep it (human, 2026-09-25); see corrections.md "[US-069] Tenancy probe users get plan features and permissions". US-069 unblocked.
  **Answer ([US-069] Pro dialog):** accept the nearest tokens (human, 2026-09-25); see corrections.md "[US-069] Pro upgrade dialog uses the nearest tokens". US-069 unblocked.
## [US-064] From Invoice: the file preview's zoom viewer (2026-09-25T23:45:11+02:00)
  **Answer ([US-064] zoom viewer):** accept the simpler viewer (human, 2026-09-26); see corrections.md "[US-064] Simpler zoom viewer". US-064 unblocked.
  **Answer ([US-071] two differences):** Upgrade Plan Click accepted with Q13; kebab hover must MATCH legacy via one new semantic token (human, 2026-09-26); see corrections.md "[US-071] Upgrade Plan C
