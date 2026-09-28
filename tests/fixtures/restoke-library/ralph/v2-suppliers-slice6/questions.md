# Slice 6 (imports, onboard suppliers, add or update items from a file): decisions
**Answered:** none yet.
## Scope and shape
1. **Port the imports, or keep linking to legacy.** Today V2's four import entries are `LegacyLink to="suppliersModal"` (V2 page-actions.tsx:28-61, 91-106), so the user leaves for the legacy list and 
2. **Flag.** Ship slice 6 under V2_SUPPLIERS with no flag of its own, as slices 4 and 5 did. FLAGGED_PAGES `legacy_url_names` documents the legacy URLs a page replaces, and test_v2_flags resolves them
3. **Story order.** The order is:
4. **Onboard suppliers (request a supplier basket).** It is not reachable in the live app:
5. **Email button leak (security, legacy, all emails).** `awsmail` mutates the module-level `email_defaults` dict in place:
6. **Step-zero holes (new).** Slice 5 Q4 sent legacy holes to adi/fix/supplier-legacy-holes (unmerged, no PR). Slice 6 found:
7. **Dialogs in the URL, deep links and auto-open.** Legacy opens these modals four ways:
8. **OAuth returns land on the legacy list.** The Xero, QuickBooks and Gmail callbacks redirect to `/u/suppliers/`:
9. **Background completion: toasts.** Legacy reports import results as database toasts, shown by base.js `update_toast_notifications()` → GET /get_toast_notifications/ after each content-loader naviga
10. **Long synchronous work, and reads that write.**
## Permissions (server side)
11. **Rules per endpoint (menu rule).**
12. **Upload: which editability.** Legacy shows the button on `not supplier.is_readonly` (supplier.html:170). So:
13. **OAuth start and callback stay legacy views.** They are browser redirects to Xero, Intuit and Google, not API reads, so they cannot live under /api/v2/.
## Behaviour where legacy looks buggy, or conflicts with a V2 rule
### Every slice-6 dialog
14. **Pending, failure and error states where legacy has none.** Legacy has:
15. **Frontend Mixpanel.** Legacy's Mixpanel tracking in these dialogs is frontend-only:
### Accounting import (US-093..095)
16. **Where "connected" is computed.** Legacy decides connection on every suppliers page render with live API calls:
17. **Tenant choice writes on a GET.** With more than one Xero tenant the dialog shows "Select your account from this list:" (import_suppliers_from_accounting_content.html:3-20). The chosen tenant goe
18. **Get Suppliers, loading, empty and fetch errors.**
19. **Search hides rows, but Create Selected sends them.** The client-side "Search..." filter (base.js:4344-4348) hides cards, yet Create Selected posts every ticked checkbox, hidden ones included (ba
20. **After Create Selected; closing the modal.**
21. **What create does to data (copy or fix).** `create_or_update_supplier_from_invoice_file` (integrations.py:257-308; invoice_parsing.py:204-328), shared with quickstart:
22. **Entry-point quirks and wording.**
### Gmail import (US-096..098)
23. **"Connected" means only that a refresh token exists.** `is_connected_to_gmail` (gmail.py:326-353). A revoked token shows "You are connected to **Gmail**" and then silently an empty list or the ol
24. **Opening the dialog always turns background consent back on.** `InboxOrdersService.__init__` calls `enable_inbox_searching()` (inbox_orders_service.py:411), so unticking "Keep checking my email f
25. **Candidate rows.** Each row shows only the raw From header ("Acme <billing@acme.com>"), grouped by exact sender string, so "Name <a@b>" and "a@b" are two rows (inbox_searching_views.py:47-57). Th
26. **Reject (trash).** It has no confirmation and no undo. The icon hides, the row fades out over 1s, then the POST is sent (supplier_candidates.html:53-60). On failure the empty response swaps into 
27. **Create Selected (Gmail).**
28. **The consent info popup.** The info icon opens legacy's #inlineModal over the import modal, "Next steps for continuous invoice streaming" with a long body starting "How auto-approval works:" (sup
29. **Not connected; OAuth failure.** The not-connected state is "Connect your email so we can automatically find your suppliers" / "Before we get started, let us look for suppliers in your email. Jus
### Add or update items from a file (US-100..103)
30. **Chooser; cancelling the file picker.**
31. **Analyse and its states.**
32. **The file between analyse and apply (session).** Legacy keeps the stored file name and the analysis in the Django session (supplier_views.py:1151, 1165). So two tabs overwrite each other, and a m
33. **Map Your Fields look.**
34. **What apply does to data.** `create_supplier_items_from_document` (supplier_views.py:1372-1504) is shared with `new_supplier`:
35. **Apply result.** On success the modal hides and the page reloads. There is no toast, count or per-row result. On failure there is nothing: the button is restored and the `.error` `<p>` is never u
36. **Upload Invoice.**
37. **Empty-basket auto-open.**
38. **Empty-file "Save" branch.** With the CSV type chosen but no file, "Analyze" is relabelled "Save" and posts an empty mapping. That saves the supplier, refreshes item types and logs an activity, w
## Audit events (Q14 of slice 4 applies: exact legacy names and properties, sent only on success)
39. **The slice-6 events.**
## Testing what dev and CI cannot reach
40. **Xero and QuickBooks.** Real OAuth and APIs cannot run in CI or e2e. The existing "mock" accounting provider (urls.py:169-173: check_mock, send_invoice_to_mock, mock_supplier_accounts) is for inv
41. **Gmail.** The fakes exist: MockInbox and MockEmail (restoke/tests/main/domain/business/email/mock_objects.py:21,133). Patch `InboxFactory.create` (inbox_factory.py:13). Seed `OAuthCredentials(app
42. **Invoice analysis (OCR/LLM).** Upload Invoice (US-103), accounting create (US-095) and Gmail accept (US-098) all run `analyze_invoice_without_postpone` / `store_and_analyze_invoice`.
## Visual compares
43. **Which states both legacy and V2 can show.** The standing rule is that a pair is only for a state legacy can reach in normal use (slice 5 Q30).
## Loop questions
