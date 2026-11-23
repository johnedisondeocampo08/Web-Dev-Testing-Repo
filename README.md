# Optimum Business Products

Inventory Tracking and Sales Management System for the product-trading operation described in the OBP proposal. This is an internal staff application; it does not provide customer accounts, repair scheduling, payroll, multi-branch stock, or general-ledger accounting.

## Features

- Staff sign-in, individual accounts, four starting roles, and server-side permission checks.
- Dashboard with daily sales, stock health, low-stock alerts, and open supplier orders.
- Product and supplier records; stock balances backed by an append-only movement history.
- Draft and ordered supplier purchase orders, cancellation, partial receiving, and duplicate-receipt protection.
- Sales, payments, partial dispatch, customer details, printable receipts, and inspected returns.
- Daily sales, monthly sales, best-selling products, and gross-profit reports with CSV export.
- Administrator-only database backup and restore, backup history, and seven-backup retention.

The public storefront lives in `OBP_website/` as a standalone HTML, CSS, and JavaScript site. Its company history, products, services, and Makati address are based on the supplied business profile, with contact details provided for the site added to its footer. The main page links to a separate, filterable sample catalog at `/OBP_website/products.html`; category cards open that catalog with the selected category applied. Listings are illustrative, not live inventory, and there is no checkout. Both pages include a speaker control for the supplied background music; visitors start or mute it with the control because browsers restrict automatic audio playback. The “Find our office” link scrolls to a Google Maps iframe, with a Google Maps directions link as an alternative. Flask serves the storefront at `/`, while staff sign in at `/login` and return to the dashboard. Tailwind utility classes control its appearance, and its JavaScript handles mobile navigation and catalog filters. Tailwind uses its browser CDN for this prototype; compile it locally before production deployment. Newsletter sign-ups are not stored. Staff-page JavaScript loads data from Flask JSON routes. Flask handles sign-in, permissions, validation, and database work. PostgreSQL stores the records.

## Run locally

Requirements: Python 3.11+ and PostgreSQL 15+. `pg_dump` and `pg_restore` must be installed and on `PATH` to use backup/restore.

1. Create a PostgreSQL database and user, or start the included database:

   ```powershell
   docker compose up -d db
   ```

2. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Configure the application in PowerShell (use a unique secret outside development):

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   Set `DATABASE_URL`, a unique `SECRET_KEY`, and `BACKUP_DIR` in `.env`.

4. Create the tables, roles, and first administrator:

   ```powershell
   flask --app run init-db
   flask --app run create-admin
   ```

   `create-admin` prompts for the username, display name, and password. Passwords are stored with Werkzeug's salted password hash. No default account or password is shipped.

5. Start the development server:

   ```powershell
   flask --app run run --debug
   ```

Open <http://127.0.0.1:5000>. To create the proposal's sample catalog, run `flask --app run seed-demo` after `init-db`.

## Daily backups

Run `flask --app run backup-database --scheduled` once each day using the operating system scheduler. On Windows, register a daily task from the project root with `.\scripts\register_daily_backup.ps1`; it runs as the current user and reads the project's `.env`. Successful backups are stored outside PostgreSQL and the seven latest are retained. The administrator page also supports a manual backup and restore from an application-created backup.

Restore replaces the active database. The administrator must enter `RESTORE` to confirm. The application creates a pre-restore backup first, validates the selected archive, attempts the restore, and uses the pre-restore archive if restoration fails. Test restore workflows against a non-production database before deployment.

## Permissions

| Role | Starting access |
| --- | --- |
| Administrator / Owner | All modules, user and role management, adjustments, refunds, backup and restore |
| Sales Staff / Cashier | Read available products, create sales, take payments, dispatch, view receipts |
| Inventory Staff | Products, stock history, suppliers, purchase orders, receiving, submit adjustments |
| Accounting Staff | Finalized sales and reports; print and export |

Roles are seeded by `init-db`. Permissions are stored in PostgreSQL and enforced by route decorators, not just hidden navigation links. Add staff through **Administration** after signing in.

## Project layout

```text
obp/
  auth.py          Sign-in and session routes
  backups.py       PostgreSQL backup and restore
  db.py            Database connection and transactions
  permissions.py   Staff role permissions
  reports.py       Sales and profit calculations
  routes.py        Website pages and JSON data routes
  schema.sql       PostgreSQL tables and role data
  static/
    pages/         Staff HTML screens
    css/           Website styles
    js/            Staff interactions and API requests
OBP_website/
  index.html       Public-facing storefront
  products.html    Separate sample product catalog
  styles.css       Storefront styles
  script.js        Storefront interactions
  assets/          Storefront images
tests/             Permission, report, and public-page tests
```

## Beginner code map

- HTML files describe the forms, buttons, and tables shown in the browser.
- `OBP_website/styles.css` controls the storefront's appearance.
- `OBP_website/script.js` handles the storefront's menu and newsletter demo state.
- `obp/static/js/app.js` responds to staff-page clicks and sends requests to Flask using `fetch`.
- `routes.py` receives those requests, checks permissions, and reads or updates PostgreSQL.
- `schema.sql` describes the tables. `backups.py` uses PostgreSQL's `pg_dump` and `pg_restore` tools.

Start by changing one page in `obp/static/pages`, then update `app.js` only if that page needs new browser behavior. The application still has database and backup safeguards because those operations can affect real records.

## Git collaboration

Use feature branches, review changes in pull requests, keep credentials and database archives out of Git, and commit schema changes with their application changes. `BACKUP_DIR`, `.env`, local virtual environments, and generated exports are ignored.

## Notes on business calculations

- Finalized sales reports include only dispatched quantities. Draft and cancelled sales do not count.
- Sale item costs are snapshotted at dispatch, so later purchase prices do not rewrite history. Gross profit is VAT-exclusive net sales less cost of goods sold; margin is N/A when net sales are zero.
- Payment collections and outstanding balances are shown separately from sales revenue.
- Restock receipts post accepted quantities only. A receipt reference can be posted once.
- Returns refer to original dispatched sale lines. Saleable returned quantities restock; defective quantities are recorded as inventory losses.
- Stock changes and their ledger entries are committed together. Manual corrections require administrator authorization and a reason.
- This academic implementation is a starting point for the proposal, not a substitute for production security review, operational backup drills, or accounting advice.
