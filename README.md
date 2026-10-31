# Thachi Investiments — Pharmacy Management System

**Working-data status:** demo records were removed from `data/pharma.db` on 29 October 2026. A read-only audit on 31 October confirmed the demo-clean marker, no sample accounts/products/batches/transactions, valid SQLite integrity and foreign keys, and balanced journals. The saved quotation, two customer records, approved exchange rate and account definitions remain. No real workspace records were deleted during the production-readiness work. See [DATA_CLEANUP.md](DATA_CLEANUP.md) for the original cleanup evidence.

A pharmacy management system for stock and batch control, sales and billing, customer accounts, cash flow, expenses, assets and reporting. Point of sale is available as one workflow under **Sales**. The app has SQLite persistence for local use, a responsive PWA and a transaction engine. **This is a substantial reference application, not a completed production pharmacy platform.** See the feature status and production prerequisites below.

## Run

Requires Python 3.11+ and a current Chromium/Firefox browser. No pip, npm, cloud account or external API key is required.

On this Windows machine, double-click `start.cmd`, enter the Gmail App Password when prompted to enable email (or leave it blank to skip email), keep its server window running, then visit **http://127.0.0.1:8000** (or **http://localhost:8000**). The frontend and API are served together on port 8000; there is no separate frontend server. The sender is `nyashamusingafi23@gmail.com`; the app password is held only in the server process environment and removed when the server exits. Alternatively:

```powershell
python server.py
# The interpreter available in this workspace:
& "$env:LocalAppData/Python/pythoncore-3.14-64/python.exe" server.py
```

On macOS/Linux: `python3 server.py`.

### If the frontend URL does not open

- Start the server before opening the URL. Closing the server window or stopping its process makes the address unavailable.
- Use `http://127.0.0.1:8000` if `localhost` fails to resolve. Use HTTP only for this local workspace, not HTTPS.
- Run commands from the project folder. On this machine, the explicit Python interpreter command above works even if `python` or `py` Windows aliases do not.
- If port 8000 is already in use, check whether Thachi Investiments is already running. To choose another port in PowerShell, run `$env:PORT='8002'`, then start `server.py` and open `http://127.0.0.1:8002`.
- Open the application in a browser on the same computer. `localhost` on a phone or another computer refers to that device, not this pharmacy server.
- For stale frontend assets, try a normal reload first. Do not clear browser storage while offline transactions are pending.

To verify the frontend from PowerShell while the server is running:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/).StatusCode
# Expected: 200
```

The server creates the schema but **never creates a default account or sample business records**. The existing `data/pharma.db` retains the real workspace records described above. For a new empty workspace, create its first administrator before starting the server:

```powershell
& "$env:LocalAppData/Python/pythoncore-3.14-64/python.exe" manage.py bootstrap `
  --admin-email admin@example.com `
  --admin-name "Pharmacy Administrator" `
  --business-name "Your Pharmacy"
```

The command prompts for a password. Store it in your password manager. Without a bootstrap administrator, the app remains at sign-in; there is no shared demo credential.

Administrator controls business details, default tax, approval threshold, exchange rates and offline policy. Manager and stock-controller accounts can be created with the local management command. Sign out before switching roles on a device. Pending transactions must be resolved before sign-out or password changes.

### Self-registration

The login screen now includes **Create an account**. In local development, online users can register their name, email and matching password (12–128 characters), then sign in. Registration joins this existing pharmacy workspace; it does not create a separate business database. New users are active **cashiers**. Privileged roles cannot be selected through self-registration and must be assigned by an administrator with the existing user-management tools. Production startup requires `PHARMA_SELF_REGISTRATION=0`.

Emails are trimmed and normalized to lowercase; existing emails cannot be registered again. Passwords use salted PBKDF2-SHA256, and account creation is recorded in the audit log without the password. The endpoint applies same-origin write checks and a limit of five registration attempts per IP per hour. Registration requires a connection. No verification or welcome email is sent because an email provider is not connected.

Self-registration is enabled by default only for local development. Set `PHARMA_SELF_REGISTRATION=0` in every production deployment. Registered users must use their password to authorize their own encrypted browser vault; pending records from another user's device workspace are never silently cleared.

The backend database remains **SQLite**, by default `data/pharma.db` (override with `PHARMA_DB`). Browser offline data is separately stored in encrypted **IndexedDB**.

## cPanel deployment foundation

`passenger_wsgi.py` is the cPanel/Passenger WSGI entry point. It serves only files from `web/`, requires an explicit production configuration and a verified demo-free database, redirects HTTP to HTTPS, checks an allowed-host list and adds browser security headers. It does not seed demo records. This is a deployment foundation, **not a claim that the pharmacy workflows are certified or ready for regulated live use**.

Use a subdomain at its own root, such as `pharmacy.example.com`; this app uses root-relative paths and is not configured for a subfolder URL. Put the application source outside the web document root, and keep the database/backups in a dedicated persistent directory outside it. In cPanel’s Python application settings, configure:

| Variable | Example | Purpose |
| --- | --- | --- |
| `PHARMA_ENV` | `production` | Enables production guards; required |
| `PHARMA_DB` | `/home/CPANELUSER/pharmacy_data/pharma.db` | Absolute path to persistent SQLite database |
| `PHARMA_PUBLIC_ROOT` | `/home/CPANELUSER/public_html/pharmacy` | Actual document root for the public subdomain |
| `PHARMA_ALLOWED_HOSTS` | `pharmacy.example.com` | Exact host names; comma-separate aliases |
| `PHARMA_SELF_REGISTRATION` | `0` | Keep public signup disabled |
| `PHARMA_SMTP_USER` | `nyashamusingafi23@gmail.com` | Gmail sender account |
| `PHARMA_SMTP_PASSWORD` | *(Google App Password)* | Optional email credential; keep it in host-managed secret settings |

In **cPanel » Software**, confirm that **Setup Python App** or **Application Manager** is available and that the host supports Python 3.11+ with Passenger/WSGI. Configure the app’s startup file as `passenger_wsgi.py`, entry point as `application`, and use HTTPS. cPanel feature availability and Passenger installation depend on the hosting provider; see its [Python WSGI deployment guide](https://docs.cpanel.net/knowledge-base/web-services/how-to-install-a-python-wsgi-application/) and [Application Manager requirements](https://docs.cpanel.net/cpanel/software/application-manager/).

Before Passenger starts, create a dedicated database directory and bootstrap a clean production database from cPanel Terminal (replace paths and details):

```sh
export PHARMA_ENV=production
export PHARMA_DB=/home/CPANELUSER/pharmacy_data/pharma.db
python /home/CPANELUSER/pharmacy_app/manage.py bootstrap \
  --admin-email admin@example.com \\
  --admin-name "Pharmacy Administrator" \\
  --business-name "Your Pharmacy" \\
  --opening-cash-usd 0 \\
  --opening-bank-usd 0
```

The command prompts for a new 16+ character administrator password, creates a non-demo administrator, USD cash/bank accounts, and no inventory, patients, or fictitious transactions. Add products, approved exchange rates, customers, opening stock and reconciled opening balances before use. To migrate the current business database, take a consistent backup, transfer it encrypted, restore it into the private persistent database path, reconcile pending offline events, and verify it with `python manage.py check`. Set the same environment variables for the Passenger application itself; shell exports from bootstrap do not automatically configure cPanel’s application environment. After code changes, restart the Passenger app as directed by the host (often by touching `tmp/restart.txt`).

Offline transactions are disabled in production until secure per-device enrollment and stock allocation exist. The cPanel deployment uses SQLite and has not had an independent penetration test, pharmacy/privacy compliance review, disaster-recovery exercise, or multi-user load test. Confirm cPanel’s persistent-storage and backup guarantees; use a managed database such as PostgreSQL before multi-worker or multi-branch operation. Do not enter live patient or prescription data until the outstanding deployment and jurisdiction-specific reviews are completed.

Remaining production blockers include independent penetration and accounting review; MFA; tested disaster recovery and alerting; per-device enrollment before offline use; a manager-facing identity/role administration workflow; opening-balance and foreign-currency account setup; partial return and jurisdiction-specific prescription workflows; and deployment-specific privacy, retention and regulatory approval. The current email sender sends a text summary only, without a PDF attachment. cPanel feature settings and network policy must be verified with the hosting provider. This deployment foundation should be treated as a **staging candidate**, not authorization to process real patient or pharmacy transactions.

## Vercel deployment

The Vercel adapter is `app.py` (FastAPI), with static browser assets staged from `web/` into `public/` by `build_vercel.py`. Vercel's function filesystem is temporary, so this deployment requires a persistent PostgreSQL database. Do not deploy with SQLite as the production database. Connect a managed PostgreSQL provider through Vercel Marketplace and use its pooled/serverless connection URL to avoid exhausting direct database connections as functions scale. Keep the connection string in Vercel's encrypted environment variables.

Before importing the project into Vercel, install `requirements.txt` in a local Python 3.12 environment. To preserve an existing SQLite workspace, take a separate encrypted backup first, then run the migration script against an **empty** PostgreSQL database:

```powershell
$env:PHARMA_ENV = 'production'
$env:DATABASE_URL = 'postgresql://...'
python migrate_sqlite_to_postgres.py data/pharma.db
```

The migration verifies SQLite integrity, foreign keys, row counts and balanced journals. It never changes the SQLite source. Keep the PostgreSQL URL private; set it in Vercel project settings as a secret, not in source control. For a new clean workspace instead, run `python manage.py bootstrap --admin-email ... --admin-name ... --business-name ...` with the same production and database environment, then provide the admin password at the prompt.

In Vercel, import the repository and set these **Production** environment variables:

| Variable | Value |
| --- | --- |
| `PHARMA_ENV` | `production` |
| `DATABASE_URL` | Managed PostgreSQL connection string from the provider |
| `PHARMA_ALLOWED_HOSTS` | Your exact production domain, such as `pharmacy.example.com` |
| `PHARMA_SELF_REGISTRATION` | `0` |
| `PHARMA_SMTP_USER` | `nyashamusingafi23@gmail.com` |
| `PHARMA_SMTP_PASSWORD` | Gmail App Password in a secret environment variable |

Vercel's assigned deployment host is also checked from its platform environment; add your custom domain to `PHARMA_ALLOWED_HOSTS` and Vercel project domains. Deploy to a staging URL first, verify sign-in, session refresh/logout, quotation/invoice workflows, email, and provider backups, then promote to the production domain. Database backups and point-in-time recovery must be configured in the PostgreSQL provider. Offline transactions remain disabled in production. Vercel deployment does not itself provide a pharmacy compliance review or make patient data handling compliant.

## Architecture and implemented plan

1. **Browser:** buildless ES modules, CSS and local SVG icons; responsive dashboard and all eight navigation sections. Service worker caches application assets. Browser IndexedDB stores encrypted snapshots and a durable outbox.
2. **Local vault:** AES-256-GCM with random 96-bit IVs; key derived from the authorized user's password with PBKDF2-SHA256 (250,000 iterations). Key is retained in sessionStorage for same-tab reloads, not in persistent IndexedDB. After browser closure, the user signs in/unlocks with the previously authorized password. This protects at-rest browser records, not a compromised running browser or operating system.
3. **API:** Python standard-library development HTTP server, bound to loopback by default, a Passenger WSGI adapter for cPanel, and a FastAPI adapter for Vercel. Production uses HttpOnly, Secure, SameSite session cookies; new passwords use PBKDF2-HMAC-SHA256 with 600,000 iterations; existing hashes are upgraded after successful login. Active-user checks, role checks, same-origin write checks and persistent login/registration/email throttles are enforced.
4. **Database:** SQLite with foreign keys, WAL, serialized writes and atomic transactions. Products/batches, documents/lines, payments, movements, journals, assets, tills, users, sessions, event inbox and audit records are linked in the schema.
5. **Accounting:** authoritative values use integer minor units and Python Decimal with half-up rounding; each posting balances in USD. Invoice/payment original currency, frozen approved rate and base equivalent are retained. Browser values are previews; the server recalculates and validates postings.
6. **Synchronization:** UUID event IDs and persisted inbox results make retries idempotent. Duplicate IDs with different payloads are rejected. Event occurrence and receipt timestamps are retained. The browser recovers interrupted `syncing` states to `pending`.

Invoice and quotation email uses Gmail SMTP (`smtp.gmail.com`, TLS port 587). Google 2-Step Verification and an App Password are required; create one in the Google Account security settings and enter it at startup. The email includes the document number, date, totals and optional message; it does not attach a PDF. Customer email must be recorded on the customer profile. Rates are approved manually. Optional external rate feeds, payment gateways, SMS and cloud storage are not connected. Google Fonts is optional presentation enhancement; system fonts work offline.

## Operational and accounting rules

### CSV uploads

Use **Upload CSV** beside the add buttons in Inventory, Sales/Customers, Expenses and Assets. The Add product, Receive stock, Add customer, Record expense and Register asset forms also have an Upload CSV shortcut. Choose an import type, download its template, select/drop a UTF-8 comma-separated file and click **Validate & preview**. Fix every invalid row, then explicitly confirm **Import N rows**.

Supported imports:

| Import | Required columns | Financial effect |
| --- | --- | --- |
| Products | `name,sku,barcode,cost,price` | Catalogue only; cost/price are USD per unit |
| Inventory batches | `sku,number,expiry,quantity,cost,supplier` | Receive stock and create USD supplier payable |
| Customers | `name` | Customer account only |
| Expenses | `description,supplier,category,currency,amount` | Post expense/payable; optional `paid` posts cash payment |
| Assets | `description,category,date,cost,currency,account_id,life` | Debit fixed assets; credit the selected payment account |

Templates include optional fields and an example row. Replace example records before importing. Prices and amounts use decimal major units (e.g. `12.50`), not exported raw integer cents; dates use `YYYY-MM-DD`; `rx` accepts true/false; quantity is whole base sales units. Import products before batches and refer to them by SKU. Asset `life` is years; its account currency must match the asset. Expand Available payment account IDs in the import dialog for valid account identifiers.

Imports require a connection and the same server role as manual entry. Files are limited to 1 MB/500 rows. Preview runs the real posting rules inside a rolled-back transaction and saves nothing. Confirmation revalidates all rows against current stock, account funds, rates and duplicate keys, then commits the entire file with its audit/import/inbox records; any invalid row blocks the entire batch. Exact previously imported CSV data is rejected, even if the filename changes. Replaying the same request UUID returns the original result. Existing rows are never overwritten. Batch numbers already present for a SKU and matching customer identities are rejected. Validation errors can be downloaded as CSV.

Use imports for new records, not historical balance migration or updates. Financial rows post at import time; a supplied asset acquisition date does not backdate the cash ledger. There are no CSV imports for posted sales, payments, account transfers, users or bulk financial reversals.

- Credit sale: debit receivables, credit sales/tax; debit COGS, credit inventory. Cash changes only with an actual receipt.
- Receipt: debit the selected currency account, credit receivables at its historical carrying value; settlement differences go to FX gain/loss. Same-currency payments apply their exact original amount.
- Expense: debit operating expense, credit supplier payable; payment separately debits payable and credits cash. Included expense tax is retained as metadata but currently expensed gross; input-tax recovery needs jurisdiction-specific implementation.
- Stock receipt: debit inventory, credit supplier payable in USD. Pay from Cash Flow → Pay supplier. Receive quantity in whole base units; pack size is stored, with explicit whole-pack multiplication performed by the operator.
- Asset acquisition: debit fixed assets, credit payment account. Straight-line depreciation is a preview until disposal, when depreciation and gain/loss are posted. It is **not** automatically included as a monthly depreciation accrual in P&L.
- Internal transfers: debit destination, credit source, post FX difference where necessary. They do not enter sales or operating costs. The cash-flow statement excludes transfers and opening balances, and classifies operating/investing/financing flows. Account movement cards deliberately show gross movements, including transfers.
- Full sale reversal: creates an immutable credit note; refunds the original amounts to original accounts; reverses revenue, COGS and receivable balance. Returns enter quarantine/returned-inventory and require pharmacist/manager inspection. Rejection writes off goods; release reclassifies to available inventory.
- Sale allocation uses FEFO across eligible batches; expired, quarantined, recalled or insufficient quantities are blocked. Every allocation is retained in `lines` and stock movements, and appears on printed posted documents.
- Prescription-only sales require an admin, manager or pharmacist and a prescription reference. References are excluded from general snapshot payloads and reports. Jurisdiction-specific controls remain to be verified.
- Posted documents have no delete or overwrite endpoint. Corrections use new documents. Drafts/quotations do not reserve stock or post accounting; converting one opens a new sale.

## Offline operating strategy and limits

Initial online sign-in, first asset caching and local vault authorization are required. Install through the browser's Install App menu where available. Secure contexts (localhost or HTTPS) are required for Web Crypto, service workers and UUIDs.

**Only the designated offline terminal should sell during an outage.** Its configured name defaults to `PHARMACY-01`. Deployment operators must give each device a unique identifier with `localStorage.setItem('device','BRANCH-TERMINAL-ID')`, reload, and authorize the chosen offline device in Settings. Identifier matching is a client/server policy check, not cryptographic hardware binding. There is no device provisioning/enrollment service or hard stock reservation by device. Other terminals must stop sales during an outage; online concurrent sales can otherwise consume cached stock.

Locally permitted actions: sales, receipts, expenses, quotations and drafts, subject to role and cached stock/rate checks. Pending sales reserve cached quantities against further offline sales in validation. Account and dashboard snapshots remain last-synced values until reconciliation; pending transactions are visible in Synchronization Centre. A provisional offline receipt carries its UUID and frozen approved rate.

Online-only actions: settings, rates, user administration, refunds, stock receiving/control/inspection, transfers, asset operations, till reconciliation and database backups. Local reports show only cached records, not authoritative unsynced totals.

The local authorization lease is configurable from 1–24 hours from the last authorized snapshot; server sessions expire after 24 hours. Device restart requires password unlock. Revocation is checked on every server request and prevents replay; an offline device cannot learn about revocation until reconnection or lease expiry. Seven-day event-age protection is an additional server safeguard, not a replacement for session/lease expiry.

Stale foreign rates block sales; approve a fresh rate online. Historical rates used in queued events remain in those events. All transactions are revalidated on synchronization. An insufficient-stock transaction becomes a visible **conflict**, with no partial posting. Manager review can reject it with a reason while retaining local evidence and downloading a resolution JSON. A corrected sale must be a new transaction. Conflict evidence and rejected events are currently local/downloaded, not a central server conflict-case registry.

Do not clear browser storage while transactions are pending. Protect/reconcile the designated terminal before resetting it. See [USER_GUIDE.md](USER_GUIDE.md) for day-to-day steps.

## Backup and restore

An administrator can download a transactionally consistent online backup through Settings. CLI backups also use SQLite's backup API, safely including committed WAL data:

```text
python manage.py backup backups/medledger-2026-10-29.sqlite
python manage.py check
```

Store encrypted off-device backups with a defined retention schedule. SQLite server files and downloaded backups are not encrypted by this application; protect them with filesystem permissions and disk/backup encryption. Automate backups using the operating system scheduler.

To restore:

1. Synchronize/reconcile pending devices, preserve their evidence and take a fresh backup.
2. Stop the application server on all ports.
3. Run `python manage.py restore backups/medledger-2026-10-29.sqlite --confirm`.
4. The command checks SQLite integrity, foreign keys and ledger balance and preserves the previous live database as `data/pharma.pre-restore.sqlite`.
5. Clear each terminal's local app storage **only after pending records have been reconciled**, then restart and sign in online. Restoring an older server snapshot while replaying an unreconciled newer device may duplicate operational intent even though UUID retries remain idempotent within the restored inbox.
6. Compare trial balance, account balances, stock ledger and a sample invoice before reopening operations.

## Users and revocation

Local operators with server filesystem access can manage identities without putting passwords into shell history:

```text
python manage.py user manager@example.com --name "Pharmacy Manager" --role manager
python manage.py revoke cashier@medledger.local
```

Password entry is hidden; minimum length is 12. Creating/updating an account revokes existing sessions. Updating a password externally requires reconciling and clearing that device vault before the next sign-in; the old password is required to recover its encrypted pending queue. Role policies are enforced in Python and in displayed controls; a graphical custom role editor is not provided.

## Verification

```text
python -m unittest discover -s tests -v
python tests/browser_check.py
```

Browser test requires local Google Chrome on Windows (override executable with `CHROME`). It launches a temporary isolated demo server/profile, creates an offline credit sale, reloads, reconnects and checks sync; it never uses the live pharmacy database. It uses Chrome DevTools and Python's standard library. See [TEST_RESULTS.md](TEST_RESULTS.md) for actual executed results and limitations. Screenshots are in `artifacts/`.

## Feature status and incomplete work

| Area | Implemented | Not yet implemented / limited |
| --- | --- | --- |
| UI | Responsive dashboard, all eight sections, date/search filters, chart, financial reports, validated CSV imports for five record types | Multiple branches, user/category/branch report filters and date-format/timezone configuration |
| Sales & point of sale | FEFO product sales, keyboard barcode scanner input, cash/credit, split currency, partial payments, drafts/quotes, statements, A4/thermal browser printing, full credit/refund | Partial returns, explicit batch override workflow, configurable invoice sequence/layout designer, direct payment processing, reliable automated change allocation |
| Cash | Separate currency accounts, opening balances, receipts, payables settlement, transfers, contributions/drawings/loans, till reconciliation, classified cash-flow report | Account creation editor, opening balance workflow for new live businesses, bank-import matching, interest/principal repayment splits |
| Inventory | Product catalogue, units/pack metadata, whole-unit sales, batch receiving, stock ledger, recalls, quarantine, counts/write-offs, return inspection, cost valuation | Purchase orders, supplier purchase returns, automatic pack conversion selector, richer configurable unit ratios, batch attachments, editing product master records |
| Expenses | Paid/unpaid/partially paid, categories, supplier, tax metadata, due date, receipt reference, CSV | Binary receipt upload, recurring templates, multi-step approval requests, recoverable input-tax postings, backdated transaction entry |
| Assets | Register, acquisition payment, straight-line value preview, full disposal journals | Scheduled depreciation posting/methods, maintenance history and binary supporting documents |
| Currency | Fixed USD base; USD/ZAR/ZiG, manual immutable rates, settlement FX, original/base reports | Selectable base migration, per-currency independent price lists, rate API integration, revaluation of open foreign balances |
| Access | PBKDF2 passwords, active/revoked accounts, roles, server checks, password change, encrypted local vault | MFA, graphical user/role management, photo uploads, central device enrollment/attestation |
| Offline | Cached shell, encrypted IDB snapshot/outbox, restart unlock, UUID/idempotent replay, retries/conflict evidence | Cryptographically signed offline permissions, per-device stock allocations, central conflict registry, cross-tab locking and coordinated multi-terminal offline sales |
| Reports | P&L, cash flow, trial balance, audit, receivables/payables, expiry, valuation, expense/asset exports, browser PDF | Native XLSX, server-generated PDF, full accounting close/revaluation, consolidated multi-branch reports |
| Hosting/security | Passenger WSGI for cPanel; FastAPI for Vercel; HTTPS/host guards, CSP/security headers, production bootstrap without demo records, role checks, persistent auth throttles, stronger password hashes, HttpOnly production sessions, local encrypted browser cache, SQLite backup/restore and PostgreSQL migration support | Independent security and pharmacy/privacy review, MFA, managed database backup monitoring, tested disaster recovery, infrastructure alerting, penetration/compliance review |

The Sites plugin's hosted runtime supports Cloudflare Workers, not this persistent Python/SQLite process. **No hosted deployment was created.** For cPanel, use the Passenger WSGI entry point and production setup above; never expose the local standard-library HTTP server directly to the Internet or use the application with live pharmaceutical/patient data before the remaining controls and reviews are complete.
