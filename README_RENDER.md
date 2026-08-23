# Portfolio Mobile - Render Free Conversion

This version removes the Windows/Excel/COM dependency from the web server.
The existing `index.html` and `admin.html` are retained and the API routes remain compatible with the current frontend.

## Architecture

Browser/Android -> Render Free FastAPI -> Supabase Free Postgres

## Important

The current Excel workbook is used only as the one-time source for migration.
After migration, Render does NOT read or write Excel.

The old app's live Excel formulas/market-data refresh are not reproduced by this conversion. The imported values are a snapshot. A separate market-data updater can be added later if required.

## 1. Create Supabase

Create a free Supabase project and copy its Postgres connection string. Use the shared pooler connection for an IPv4-only runtime if necessary.

Run `schema.sql` in the Supabase SQL Editor.

## 2. Migrate Excel from Windows

On your PC, install Python dependencies:

    pip install -r requirements.txt

Set environment variables in the same Command Prompt:

    set DATABASE_URL=YOUR_SUPABASE_CONNECTION_STRING
    set MIGRATION_PASSWORD_HASH_SECRET=YOUR_LONG_RANDOM_SECRET

Then run:

    python migrate_excel_to_postgres.py --portfolio "E:\PROJECT\data.xlsm" --clients "E:\PROJECT\CLIENT_PHONENUMBER.xlsx" --requests-db "E:\PROJECT\portfolio_mobile_stage1\portfolio_requests.db"

The script only reads the Excel files. Keep the originals as backups.

## 3. GitHub

Upload this folder to a new GitHub repository. Do NOT upload `.env`, passwords, database URLs, API keys, or Excel files containing client data.

## 4. Render

Create a Web Service from the GitHub repository.

Build command:

    pip install -r requirements.txt

Start command:

    uvicorn render_server:app --host 0.0.0.0 --port $PORT

Choose the Free instance.

## 5. Render environment variables

DATABASE_URL = Supabase Postgres connection string
PORTFOLIO_SESSION_SECRET = a long random secret
MIGRATION_PASSWORD_HASH_SECRET = EXACTLY THE SAME SECRET USED DURING MIGRATION
PORTFOLIO_ADMIN_USER = your admin username
PORTFOLIO_ADMIN_PASSWORD = your strong admin password
PORTFOLIO_1312_PASSWORD = strong password
PORTFOLIO_1313_PASSWORD = strong password
PORTFOLIO_1304_PASSWORD = strong password

The first three are required. Supervisor variables are optional if those accounts are not needed.

## 6. Test

Open:

    https://YOUR-SERVICE.onrender.com/

Admin:

    https://YOUR-SERVICE.onrender.com/admin

Health:

    https://YOUR-SERVICE.onrender.com/api/health

## Free-plan limitation

Render Free web services are suitable for testing/hobby use and may spin down when idle. Do not treat the free tier as a production-grade financial-data hosting arrangement.
