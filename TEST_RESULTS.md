# Verification results

Executed on **29 October 2026**, Windows, Python **3.14.7**, local Google Chrome headless, with isolated temporary databases/browser profiles. The live demonstration database was not used for transaction tests.

## Transaction engine — 16 / 16 passed

Command: `python -m unittest discover -s tests -v`

Final result: `Ran 16 tests in 27.836s — OK`.

| Test | Verified outcome |
| --- | --- |
| Cash sale atomicity | Stock decreases, revenue/COGS post, correct account increases, journals balance |
| Credit and partial payment | Credit sale leaves cash unchanged; payment reduces receivable correctly |
| Full refund and inspection | Cash restored, original stock not automatically restocked, quarantined return released only through inspection |
| Frozen currency rates | New rate does not change existing invoice rate; settlement journals balance |
| Foreign partial rounding | Cumulative historical-rate rounding clears the receivable exactly after multiple partial payments |
| Stock eligibility | Expired, quarantined and insufficient stock cannot be sold; failed posting leaves document count unchanged |
| FEFO, recall, stale rates | Earliest eligible batch selected, recalled batch excluded, stale FX rate rejected |
| Idempotent replay after DB reopen | Persisted event replays exactly once without a second stock decrement |
| Stock conflict atomicity | Conflict creates no document, event success or journal fragments |
| Dashboard/report reconciliation | Sales totals match revenue ledger and every account's journal matches payment base totals |
| Overpayment rollback | Excess payment rejected without changing account balance |
| Transfers | Internal transfer does not increase sales; journals remain balanced |
| Roles and prescriptions | Cashier cannot post expenses or authorize prescription-only sales |
| Financial data visibility | Cashier snapshot excludes purchase costs, journals and account balances |
| Goods receipt / supplier payment / count | Supplier payable is created and partly paid; count updates stock and balanced entries |
| Backup integrity | SQLite backup reopens with integrity `ok` and matching document counts |

## Browser workflow — passed

Command: `python tests/browser_check.py`

- Online sign-in and dashboard rendering.
- Sales, Cash Flow, Assets, Expenses, Inventory, Profile, Settings and Reports navigation.
- Cash sale through the UI, payment entry, posted invoice and generated [invoice PDF](artifacts/invoice.pdf).
- Unpaid expense submitted through its form and displayed from persisted server state.
- Offline credit sale and printable provisional receipt.
- Durable queue and authorized workspace survive reload while offline.
- Clearing sessionStorage (simulating loss of the session key on browser restart), reloading offline and unlocking with the authorized password recovers the pending queue.
- IndexedDB queue is stored as AES-GCM ciphertext rather than plaintext records.
- Reconnect/retry returns the pending counter to zero.
- 390-pixel mobile viewport has no horizontal page overflow.
- No uncaught JavaScript exceptions during the tested sequence.

Screenshots: [desktop dashboard](artifacts/dashboard.png), [mobile dashboard](artifacts/mobile.png).

## Additional checks

- ES-module and service-worker syntax checks passed using the installed Electron Node runtime (`--check`).
- Actual HTTP root request: **200**; administrator login and authenticated snapshot retrieval succeeded.
- `python manage.py check` on the demonstration database: **Integrity: ok; Foreign key errors: 0; Unbalanced sources: 0**.

## Verification boundaries

The browser test simulates restart by removing the session key and reloading; physical power loss, storage-controller failure, printer hardware, real barcode devices, installation on every platform and full multi-device network partitions were not exercised. Server replay is tested after closing/reopening the database. Backup reopening/integrity is tested, but a live production disaster recovery drill and automated scheduled backup retention are not.

Browser coverage is a meaningful smoke workflow, not an exhaustive test of every form permutation, role, accessibility interaction or currency combination. The immutable server event ID covers interrupted-response retries; transport failures at every possible byte boundary were not injected. Cross-tab coordination, cryptographic terminal identity and multi-terminal offline stock reservations are not implemented. No penetration test, clinical/regulatory review, payment-gateway certification or production deployment verification was performed.

See README's feature matrix for features intentionally identified as incomplete. Passing these tests does not make those missing features complete or establish production compliance.

## CSV import and branding update

After adding CSV imports, **24 / 24 backend tests passed** (`Ran 24 tests in 34.737s — OK`). Eight additional import tests cover preview rollback, product persistence, exact UUID replay, duplicate-data blocking, whole-file rollback, batch stock/payable/movement links, cumulative payment-account funds, quoted multiline/BOM CSV, malformed headers/rows, asset acquisition, role enforcement, offline blocking and file limits.

The updated browser workflow also passed actual CSV file selection, side-effect-free product preview, product import, batch import through the Receive stock form shortcut, and invalid-row blocking with validation-error export. Existing sale, expense, offline recovery, encryption, reconnect and mobile smoke checks all passed again with the Thachi Investiments branding and supplied logo in place.

## Reference invoice layout

Targeted browser verification of the new reference-based invoice passed: medical-blue header colour is `rgb(37, 99, 235)`; the paid sample's balance is `$0.00`; A4 output is **one page**; the invoice fits a 390-pixel mobile viewport; and no uncaught JavaScript exceptions were recorded. Existing sale data was only viewed, not changed during this check. The dark-sidebar logo treatment does not apply to the colour logo on invoices.

Artifacts: [invoice preview](artifacts/invoice-reference-layout.png), [A4 invoice PDF](artifacts/invoice-reference-layout.pdf). The file supplied as the updated logo contained the invoice reference screenshot, so the existing Thachi capsule logo was retained.

## Final login, capsule and invoice updates

**25 / 25 backend tests passed** (`Ran 25 tests in 46.105s — OK`), including the new customer billing-address persistence and frozen tax-percentage test. The complete browser smoke workflow passed again after the login redesign: sign-in, all module navigation, CSV imports/validation, online sale, expense entry, encrypted offline queue recovery, password unlock, reconnect synchronization and mobile overflow checks.

Targeted login checks passed Show/Hide password, administrator-help modal, explicit demo-account selection, mobile logo visibility and sign-in. Desktop/mobile screenshots: `artifacts/login-reference-desktop.png` and `artifacts/login-reference-mobile.png`.

A read-only reference invoice preview verified THACHI INVESTMENTS, 3 NEWPORT / AVENUE HARARE, TIN 2001799978, PENDULUM PHARMACEUTICALS, BLUFFHILL INDUSTRIAL PARK / HARARE, amanda@kedivine.com, Due Upon Receipt, unit price `6,00`, total `120,00` and tax rate `0,000%`. It printed to **one A4 page** with no uncaught JavaScript errors. See `artifacts/invoice-template-sample.pdf` and `artifacts/invoice-template-sample.png`. No invoice 670 or sample sale was posted to the live database by this preview.

## Invoice eye action and current supplied logo

The browser regression workflow passed with the new invoice detail page and current logo. Additional checks covered the eye action, bookmarked detail route after reload, original terminal metadata, PDF preview, delivery note without unit prices, customer statement, back navigation and mobile overflow. The existing CSV, cash sale, expense, encrypted offline restart/unlock, reconnect and mobile checks passed in the same run. The reconnect wait allows 30 seconds to accommodate the local browser environment.

Screenshot: [invoice detail page](artifacts/invoice-details.png). `web/brand-logo-current.png` is the user's new image copied without modification; its first exterior pixel is RGBA `(0,0,0,0)`, confirming transparency. Dark-surface styling and the application's blue palette are preserved.

## Pale-blue receipts and confirmation pop-ups

The supplied local PDF was rendered and inspected with Ghostscript. The receipt now uses the reference's pale-blue `#DBEAFE` header/totals fill, navy `#172554` text, and light-blue rules. Posted and offline provisional receipts share the updated layout, and the redundant sidebar caption is removed.

The expanded browser workflow passed the absence of that caption; exact pale-blue/navy computed colours; refund-confirmation cancellation preserving the entered reason without posting; red trash-icon strokes (`rgb(185,28,28)`); Cancel retaining a cart item; explicit confirmation removing it once; a green success notification; and Cancel retaining an unposted payment. Cancel receives initial keyboard focus. The existing invoice, CSV, sale, expense, offline recovery/reconnect and mobile checks also passed, with no uncaught JavaScript exceptions.

Artifacts: [confirmation dialog](artifacts/delete-confirmation.png), [pale-blue receipt](artifacts/receipt-pale-blue.png), and the refreshed generated invoice at `artifacts/invoice.pdf`.

## Self-registration

**29 / 29 backend tests passed** (`Ran 29 tests in 60.133s — OK`). Four additional registration tests cover normalization, unique email enforcement, password confirmation/length, salted password hashing, audit redaction, active cashier defaults, privileged-role rejection, environment-based disabling, actual HTTP registration/sign-in, financial permission enforcement, cross-origin rejection and registration throttling.

The complete browser workflow passed again, including Create an account, password mismatch validation, successful new-user sign-in, cashier financial-access restrictions and confirmed sign-out before returning to the administrator. Existing module navigation, invoice actions, CSV, confirmations, pale-blue receipts, encrypted offline recovery, reconnect and mobile checks also passed with no uncaught JavaScript exceptions.

## Demo-free production startup — 31 October 2026

The backend suite passed **34 / 34 tests** (`Ran 34 tests in 89.466s — OK`), including new checks that normal initialization creates no default user or demo records and that explicit demo seeding is rejected in production. Python syntax compilation passed for the server, both deployment entry points, management/migration scripts and backend test modules.

A read-only audit of the working SQLite database found no demo users, products, batches or sample transactions; SQLite integrity and foreign keys were clean and journal sources balanced. Existing user data (including the quotation and customer records) was preserved. Startup now requires an explicitly bootstrapped administrator in a fresh workspace; the Vercel and Passenger entry points also enforce the demo-clean marker, active administrator, USD setup and disabled production self-registration.

No Vercel deployment or production database was created or verified in this run. Hosting credentials and a managed PostgreSQL connection must be configured, and the independent security, privacy, disaster-recovery and pharmacy workflow reviews listed in README remain outstanding.
