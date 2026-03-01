# First steps to get the platform running

Do these in order. The "website" is the app running on your Mac – you don’t set up a separate site; once the server is running, you open it in your browser.

---

## Step 1: PostgreSQL and database

You need PostgreSQL installed and a database for the app.

1. **Install PostgreSQL** (if you don’t have it):
   - Mac: `brew install postgresql@14` (or from postgresapp.com), then start the service.
2. **Create the database and user** (in a terminal):
   ```bash
   # If using Homebrew PostgreSQL:
   createuser -s postgres   # if needed
   createdb backhaul_dev
   psql -d backhaul_dev -c "CREATE USER backhaul_user WITH PASSWORD 'backhaul_pass';"
   psql -d backhaul_dev -c "GRANT ALL PRIVILEGES ON DATABASE backhaul_dev TO backhaul_user;"
   psql -d backhaul_dev -c "ALTER DATABASE backhaul_dev OWNER TO backhaul_user;"
   ```
   (If you use a different user/password, put them in `.env` in the next step.)

---

## Step 2: Environment file (`.env`)

1. In the project folder, open the file **`.env`** (create it if it doesn’t exist).
2. Put this in it (change if your Postgres user/password/port are different):
   ```
   DATABASE_URL=postgresql+psycopg2://backhaul_user:backhaul_pass@localhost:5432/backhaul_dev
   PLATFORM_FEE_PERCENT=0
   ```
   (Keep `PLATFORM_FEE_PERCENT=0` for the pilot.)
3. Save the file.  
   Make sure there are no extra characters (e.g. `›`) at the end of lines.

---

## Step 3: Python environment and dependencies

1. Open a terminal and go to the project:
   ```bash
   cd /Users/mac/Desktop/Platoonixcursor
   ```
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

## Step 4: Create database tables

Still in the same terminal (with `.venv` active):

```bash
python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
```

You should see no errors. That creates the tables the app needs.

---

## Step 5: Start the app (the “website”)

In the same terminal:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

When you see something like `Uvicorn running on http://0.0.0.0:8000`, the app is running.

---

## Step 6: Open it in your browser

Go to: **http://127.0.0.1:8000/**

That’s your console – add hauliers, vehicles, loads, find backhaul, etc. The “website” is this app; there’s nothing else to set up for local use.

---

## Order summary

| Order | What |
|-------|------|
| 1 | PostgreSQL installed + database `backhaul_dev` (+ user if needed) |
| 2 | `.env` with `DATABASE_URL` and optional `PLATFORM_FEE_PERCENT=0` |
| 3 | Python venv + `pip install -r requirements.txt` |
| 4 | Create tables (one-line `python -c ...` command) |
| 5 | Run `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` |
| 6 | Open http://127.0.0.1:8000/ in the browser |

After this, you can move on to domain, hosting, and going live (see README “Next steps to make it live”).
