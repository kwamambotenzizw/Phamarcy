# Demo-data cleanup — 29 October 2026

The requested cleanup was completed on the working SQLite database, `data/pharma.db`. A full, consistent backup was created first:

`backups/before-demo-cleanup-20261029-145605.sqlite`

The cleanup was rehearsed on a temporary copy before applying it to the working database. The maintenance operation was explicitly authorized by the user's request and logged in the audit trail; it is not a normal invoice-delete endpoint.

| Records | Before | After |
| --- | ---: | ---: |
| Products | 10 | 0 |
| Stock batches | 12 | 0 |
| Documents | 19 | 1 |
| Business assets | 1 | 0 |
| Payments | 20 | 0 |
| Accounting journal rows | 106 | 0 |
| Event inbox records | 21 | 2 |
| Customers | 5 | 2 |
| Users | 5 | 2 |

Removed: 14 bundled sample sales, 4 sample expenses, the sample refrigerator, demo opening balances, demo stock movements, demo products/batches, three example customers, three example staff accounts and unapproved demonstration ZAR/ZiG rates.

Preserved: business branding/address/TIN, settings and account definitions; the administrator's existing sign-in credentials; the actual registered user; the user-created customer; the required walk-in customer; and the user's saved quotation `QUO-2610-D0088638`. The administrator's example display name was changed to Administrator.

The saved quotation has its original product descriptions and unit prices attached as snapshots. Its subtotal remains **USD 28.20**. It can be viewed and printed even though those demo products are no longer in the active catalogue. Creating a new sale from it requires current catalogue products; removed products cannot silently return to stock.

All five payment-account balances are **zero**. USD remains the fixed base currency. Managers must configure an approved foreign-currency rate before using ZAR/ZiG.

Protection: the cleaned database has a persistent `demo_data_cleared` marker, so restart initialization does not recreate samples. A new data generation identifier prevents operational requests saved before cleanup from being replayed into the reset workspace. Pending old offline requests are rejected for explicit review rather than silently discarded or reapplied. Active sessions were revoked so users must sign in again and refresh their cached snapshot; existing account passwords were not changed.

Verification: SQLite integrity `ok`, no foreign-key errors, zero posted operational documents/payments/journals/inventory/assets, all account balances zero, and restart initialization leaves those totals unchanged. A read-only browser check verified the cleaned dashboard and the retained quotation's detail/PDF preview without adding sample records to the working database.

Do not clear local browser storage while unresolved offline records exist. Sign in online with the same account, review any conflicts, and retain reconciliation evidence. The backup intentionally still contains pre-cleanup demo data and must be protected like other database backups. Follow README's server-stop restoration procedure only if the cleanup needs to be undone.

## Follow-up audit — 31 October 2026

A later read-only audit confirmed the live database remains demo-free. It contains one user, two user-created customers plus the required walk-in customer, the saved quotation, five synced user events, five account definitions and the USD base rate plus one manager-approved ZiG rate. It contains zero demo accounts, products, batches, posted sales/expenses/payments, journal rows and assets. SQLite integrity is `ok`, foreign-key errors are zero and journal sources are balanced. The database was not modified by this audit. Counts differ from the immediate post-cleanup table above because legitimate workspace actions continued after 29 October.
