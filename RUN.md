# Start the website (step by step)

The app does **not** run by itself. You have to start the server on your machine (or later, on a host). Follow these steps.

---

## Step 1: Open a terminal

- On Mac: **Terminal** (or the terminal inside Cursor: **View → Terminal**).

---

## Step 2: Go to the project folder

```bash
cd /Users/mac/Desktop/Platoonixcursor
```

---

## Step 3: Activate the virtual environment (if you use one)

```bash
source .venv/bin/activate
```

(You should see something like `(.venv)` at the start of the line.)

---

## Step 4: Make sure the database is set

Your `.env` file in the project root should contain:

```
DATABASE_URL=postgresql+psycopg2://backhaul_user:backhaul_pass@localhost:5432/backhaul_dev
```

PostgreSQL must be running and the database `backhaul_dev` must exist. If you haven’t created the tables yet, run once:

```bash
python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
```

---

## Step 5: Start the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

You should see something like:

```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

---

## Step 6: Open the website in your browser

- **Main console (UI):**  
  **http://127.0.0.1:8000/**  
  or  
  **http://localhost:8000/**

- **API docs:**  
  http://127.0.0.1:8000/docs  

If the site doesn’t load:

- Check the terminal for errors (e.g. database connection, missing module).
- Make sure you’re using **http://** and the port **8000**.

---

# Where to change the platform fee % (e.g. 0% pilot or 8% live)

You change the fee **in Cursor (or your editor)**, not on the website. The website only **displays** the current value.

## Option A: Using the `.env` file (recommended)

1. In Cursor, in the **project root** (same folder as `README.md`), open the file **`.env`**.
2. Add or edit this line:
   - **Pilot (no fee):**
     ```
     PLATFORM_FEE_PERCENT=0
     ```
   - **Live (8% fee):**
     ```
     PLATFORM_FEE_PERCENT=8
     ```
3. Save the file.
4. **Restart the server** (in the terminal: stop with `Ctrl+C`, then run `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` again).
5. Refresh the website; the “Platform fee” line will show the new %.

## Option B: Using code (default only)

1. In Cursor, open **`app/config.py`**.
2. Find the line:
   ```python
   platform_fee_percent: float = 8.0
   ```
3. Change the number, e.g. to `0.0` for pilot:
   ```python
   platform_fee_percent: float = 0.0
   ```
4. Save and restart the server.

**Summary:** Prefer **Option A (`.env`)** so you can switch between 0% and 8% without touching code. The website will always show whatever value is in config (from `.env` or the default in `config.py`).
