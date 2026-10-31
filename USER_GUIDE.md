# Thachi Investiments user guide

## Pharmacy management workspace

This app brings together inventory and batch tracking, sales and billing, customer accounts, cash flow, expenses, assets and reports. **Point of sale is one workflow in the Sales section**, alongside invoices, quotations, credit notes and receivables.

## Start and sign in

Run `start.cmd` and open http://localhost:8000. Sign in with the administrator account created for this workspace. Fresh databases do not contain default accounts or demonstration records; an administrator must run the bootstrap command in README.md first. The first login requires a server connection. After authorization, this browser has an encrypted local workspace. After closing the browser, the same password unlocks cached data offline for the remaining authorization period.

The reference-style login has a Show/Hide password button. Remember me remembers your email, not your password or an extended session. Forgot password and Contact your administrator provide administrator-managed access guidance.

To register yourself, sign out of any current user, click **Create an account**, enter your full name/email and a matching password of at least 12 characters, then select Create account. The new email is filled into the sign-in form after success; enter your password to sign in. Registration requires a connection and creates cashier access to this pharmacy. Ask your administrator for any additional role permissions. Passwords and duplicate emails are checked on both the form and server; no verification email is sent by this implementation.

The header shows the user, role, connectivity and reporting currency. The bottom of each main screen shows pending transaction count and last successful synchronization. Open the bell or sync link for queue status and retries. Currency selector changes reporting presentation using current approved rates; invoices and payments retain their recorded currencies/rates.

## Dashboard and reports

Choose Today, This week, This month, All time or a custom date range. The dashboard distinguishes accrual sales/profit from account receipts. Cost of goods comes from the actual batch allocated to a sale. Expense totals include unpaid expenses. Balances and stock alerts are current/last-synced snapshots rather than historical date-end balances.

Export report opens Reports. P&L and trial balance share the same base-currency ledger as dashboard metrics. Open cash-flow, receivables, payables, expiry, inventory valuation and audit reports. Export CSV to a spreadsheet, or Print → Save as PDF. CSV money fields from raw transaction exports are integer cents; dedicated report columns labeled USD are decimal dollars.

## Make a sale

1. Sales → New sale. Search name, generic or barcode. A USB barcode scanner that types into the search field and sends Enter is supported.
2. Add eligible products and enter quantities in individual base units. A pack of 10 tablets is 10 units. Unavailable/expired/quarantined stock cannot be sold.
3. Select a registered customer for credit; walk-in sales must be fully paid. Add a customer from the Sales screen.
4. Select invoice currency, enter a permitted discount and record a prescription reference where required. A pharmacist/manager/admin must complete prescription-only sales.
5. Add one or more payments. Choose the receiving account and record its currency amount. The app uses approved exchange rates. To give cash change, record only the amount retained; excessive payments are rejected. Change due is previewed when entered payments exceed the total, prompting correction before posting.
6. Complete sale, or save a draft/quotation without financial or stock posting. Drafts and quotes may be opened in the POS to create a new sale.
7. Print the A4 invoice or thermal receipt. Browser printing requires a configured printer; Save as PDF uses the browser print dialog.

The A4 invoice follows the supplied Thachi example: business/logo at the upper left, invoice title and number/date at the right, Bill To and Customer ID/Terms bands, ruled item rows, and a shaded totals table. It uses the application's medical blue and navy instead of the example's green. Business details and optional TIN come from Settings. Customer names and contact details come from the selected account; the sample customer's details and invoice number are never copied into live invoices. Posted prices and totals use saved transaction values.

The updated template shows decimal-comma amounts such as `6,00` and `120,00 $`, a `TAX RATE` row such as `0,000%`, and the customer's billing address/email. Enter the address when adding a customer or importing customers with the optional `address` CSV column. Business address accepts multiple lines; the configured TIN is `2001799978`. Default invoice terms are Due Upon Receipt and new POS invoices are due today; existing saved due dates remain unchanged. Invoice date/number and product quantities always come from the actual document.

Receipt styling now follows `Thachi_Invoice_Pale_Blue.pdf`: pale-blue header bands, navy labels, pale-blue totals and light-blue item rules. Posted previews and provisional offline receipts use the same presentation; thermal printing remains available for posted invoices. The separate duplicate company-name caption below the sidebar logo has been removed.

## Operation notifications and confirmations

Successful operations show a green notification; errors show a red notification. Red trash icons remove unposted cart items or payment entries and ask for confirmation first. Cancel or Escape keeps the original data. Confirm is required again before credit/refund posting, asset disposal, stock counts/write-offs, return inspection decisions, batch-status changes, rate approval, account transfers and till closure. Password changes and sign-out also have explicit confirmations. A confirmation opens above the current form, preserving its entered fields if cancelled.

Posted invoices remain retained; credit notes are the supported correction method. Rejected offline transactions keep their evidence and require a reconciliation reason rather than being silently deleted.

Document history is searchable. Open an unpaid/partial invoice → Receive payment. The payment updates receivables and the receiving account without recording revenue twice. Customers → View statement shows invoices, statuses and balances.

### Invoice eye action and details

Sales → Invoices (or All transactions) includes an **Actions** column. Click the eye icon to open that invoice's detail page. The page shows invoice/due dates, currency, branch, original terminal and creator when recorded, reference, customer contacts, line items, allocated discounts/tax, totals, payment history and batches/expiries. Its `#sales/invoice/<id>` link can be bookmarked or reloaded and remains subject to the signed-in user's access and locally available records.

PDF opens the branded printable document preview. Print opens the browser print dialog. Email sends the invoice or quotation summary directly through `nyashamusingafi23@gmail.com`; enter an optional note and confirm the recipient before sending. The email includes document number, date, total, paid amount and balance, but does not attach a PDF. Email is available when the customer has a saved address and Gmail has been configured at server startup. Delivery note prints quantities/batches without prices, with space for the recipient's signature. Customer information links to the customer's statement. Authorized users can receive outstanding payments or issue a full credit note from the detail header.

To enable sending, turn on Google 2-Step Verification and create a Google App Password. Start the system with `start.cmd` and enter that App Password at the secure prompt. The password is held only by the running server and is not saved in the database or a file. Leave the prompt blank to run without email sending. If sending fails, check the internet connection, recipient address and App Password.

Invoices are retained and cannot be silently deleted. Credit notes are the supported correction workflow. Fiscalization is not connected; the reference screenshot's fiscalize/delete controls were not added as nonfunctional actions.

## Correct a completed sale

Admin/manager: open the invoice → Issue full credit → enter reason. The original sale is retained and marked reversed. Original payments are refunded to their original accounts, subject to sufficient funds. This implementation reverses the entire invoice, not selected lines.

Inventory → Batches → find the `-RETURN` batch → Inspect return. Record inspection findings and release sealed eligible stock or reject/write it off. Returns cannot become available automatically, and expired stock cannot be released.

## Inventory

**Bulk upload:** Use Upload CSV on the relevant screen or add form. Choose Products or Inventory batches, download the matching template, replace its example data, upload a UTF-8 CSV, then Validate & preview. Errors show their CSV row numbers and can be exported. Nothing is saved until you confirm Import N rows, and any invalid row blocks the complete file. Use whole-unit quantities, USD cost/price and ISO dates. Import catalogue products before their stock batches using matching SKUs. Uploads add records; they do not edit existing rows or load a historical accounting balance.

The same workflow supports Customers (Sales), Expenses and Assets. Paid expenses and asset acquisitions move real account balances. The dialog lists available payment account IDs. Imports require a connection and an authorized role. Maximum 500 rows/1 MB; identical previously imported data is blocked.

Add product with SKU/barcode, strength/form, base-unit cost and price, reorder threshold and purchase pack size. Receive stock records supplier invoice payable, batch, expiry and quantities in units. USD is the supported stock acquisition currency. For 5 packs of 10, explicitly enter 50 units. Use Cash Flow → Pay supplier to settle a stock invoice.

Product details show batches and locations. Batches/Expiry alerts show eligibility and dates; Trace batch shows recorded movements. Stock controls supports recall, quarantine and release with a reason, or a physical-count adjustment. To record damaged/expired stock, enter the remaining physical units and a clear write-off reason. Quantities and valuation change with a balanced gain/loss entry. Low stock and Stock ledger views are searchable and exportable.

## Cash flow and tills

Each drawer/bank/mobile-money account has one currency. Transfer records sent and received amounts in their account currencies; it does not create income or an expense. Cash entry handles owner funds/drawings and loan principal. Do not record a duplicate cash entry for a sale/expense that already has a payment.

Manage till → Open captures the current account ledger balance. Close after counting physical cash; expected, counted and variance are retained. Investigate variances using account movements rather than changing posted records.

## Expenses and assets

Expenses → Record expense: description, supplier, category, currency, total, included tax, payment now/account, due date and receipt reference. Zero payment creates an unpaid expense. Use Make payment later for a partial/full settlement. Payment now is in payment-account currency. Assets and inventory purchases belong in their own modules.

Assets → Register asset records a paid acquisition with life/residual value, location and custodian. Its account must match acquisition currency. Depreciation and net book value are informational straight-line previews; periodic accrual posting is not yet available. Dispose records proceeds and posts depreciation, removal and gain/loss.

## Offline work

Authorize only the designated terminal and stop other terminals from selling during an outage. Observe the offline lease and approved rate date. The outbox is encrypted and persists across reload/browser restart. Provisional offline receipts are available immediately. Pending activity does not appear in final dashboard/account totals until validated on sync.

On reconnect the outbox retries automatically. Review conflicts in Synchronization Centre. A conflicting sale posts no server stock or accounting changes. Manager: review the original event, reconcile physical stock/customer funds, then mark rejected with a reason. Keep the downloaded evidence; record a corrected sale separately. Never overwrite a completed sale or clear browser data to hide a failed transaction.

If a session has expired, sign in online as the original user to synchronize. If that user has been revoked, a local administrator must manage recovery/reconciliation; automatic privilege transfer is not supported. Never clear a device with pending records.

## Settings, passwords and backups

Settings changes require an online authorized role. Manual rates are USD per 1 foreign unit, with source/effective date. Old invoice values do not change. USD base is fixed. Update business details used on printed documents and verify jurisdictional tax/pharmaceutical policy before live use.

Profile updates name/email/password. Changing the password requires the current password and no pending queue, revokes server sessions and clears this local vault; sign in online with the new password. Changing email without signing out may require vault reauthorization; reconcile queues before changing identity.

Admin: download a database backup from Settings; store it encrypted off-device. Follow README's server-stop and integrity-check restoration procedure. CLI `manage.py` provides backup, restore, check, user creation and revocation. The full feature limitations are documented in README; this is a reference implementation for review, not a production compliance claim.
