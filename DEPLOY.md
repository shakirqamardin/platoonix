# Step-by-step: Are we ready to deploy? Then deploy.

---

## Part 1: Are we ready? (Checklist)

Go through this. If everything is “yes”, you’re ready to deploy.

| Check | What to verify |
|-------|----------------|
| App runs locally | You can open http://127.0.0.1:8000/ and use the console (add data, find backhaul). |
| Database works | You created tables and the app reads/writes (e.g. hauliers, vehicles, loads). |
| No secrets in code | Your `.env` is **not** in git (add `.env` to `.gitignore` if you use git). You will set secrets in the host’s dashboard. |
| requirements.txt | Present and complete (the host will run `pip install -r requirements.txt`). |
| Start command | The host will run: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (or we use the Procfile). |

If all good, continue to Part 2.

---

## Part 2: Deploy step by step (using Railway)

Railway gives you a public URL and a Postgres database. Do the following in order.

### Step 1: Create a Railway account

1. Go to **https://railway.app**
2. Click **Login** (or **Start a new project**).
3. Sign in with **GitHub** (recommended so you can connect your repo) or email.

---

### Step 2: Create a new project and add PostgreSQL

1. In the Railway dashboard, click **New Project**.
2. Choose **Deploy PostgreSQL** (or **Add plugin** → **PostgreSQL**).
3. Wait until the database is created. Click the **Postgres** service.
4. Open the **Variables** or **Connect** tab and copy the **connection URL**. It looks like:
   ```
   postgresql://postgres:xxxxx@containers-us-west-xxx.railway.app:5432/railway
   ```
5. You need it in this form for the app (with `+psycopg2` and `postgresql://`):
   - If the URL is `postgresql://user:pass@host:5432/railway`, use:
   ```
   postgresql+psycopg2://user:pass@host:5432/railway
   ```
   (Same URL but with `+psycopg2` after `postgresql`.) Save this as your **DATABASE_URL**.

---

### Step 3: Add your app to the project

**Option A – Deploy from GitHub (recommended)**

1. In the same project, click **New** → **GitHub Repo** (or **Deploy from GitHub**).
2. Authorize Railway to access GitHub if asked.
3. Select the repository that contains your Platonix code (e.g. **Platoonixcursor** or the repo you pushed).
4. Railway will create a new “service” for this repo and start building.

**Option B – Deploy with Railway CLI (no GitHub)**

1. Install the CLI: **https://docs.railway.app/develop/cli**
2. In Terminal, go to your project folder:
   ```
   cd /Users/mac/Desktop/Platoonixcursor
   ```
3. Log in and link the project:
   ```
   railway login
   railway link
   ```
4. Deploy:
   ```
   railway up
   ```

---

### Step 4: Set environment variables for the app

1. In Railway, click the **service** that is your app (the one from the repo or `railway up`), not the Postgres service.
2. Go to the **Variables** tab.
3. Add:

   | Name | Value |
   |------|--------|
   | `DATABASE_URL` | The URL from Step 2, in the form `postgresql+psycopg2://user:pass@host:5432/railway` |
   | `PLATFORM_FEE_PERCENT` | `0` (for pilot) or `8` (when you go live) |

4. Save. Railway will redeploy the app if it was already building.

---

### Step 5: Set the start command (if needed)

1. Click your **app service**.
2. Open **Settings** (or the **Deploy** configuration).
3. Find **Start Command** or **Build Command**.
4. If the app doesn’t start automatically, set the start command to:
   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
   (Railway sets `$PORT`; usually 8000.)

If you use the **Procfile** in the repo, Railway may pick it up and you might not need to set this manually.

---

### Step 6: Create the database tables (one-time)

After the first successful deploy:

1. Railway gives you a way to run a one-off command (e.g. **Settings** → **One-off command** or a “Run command” in the deploy logs). Or use **Railway CLI** in your project folder:
   ```
   railway run python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
   ```
2. That creates all tables (hauliers, vehicles, loads, jobs, payments, etc.) in the hosted Postgres.

---

### Step 7: Get your public URL

1. Click your **app service**.
2. Open **Settings** → **Networking** or **Generate domain**.
3. Click **Generate domain** (or use the default if one is already there).
4. Copy the URL, e.g. `https://yourapp.up.railway.app`.

---

### Step 8: Test the deployed app

1. In a browser open: **https://yourapp.up.railway.app**
   - You should see the Backhaul Logistics Console.
2. Open: **https://yourapp.up.railway.app/health**
   - You should see `{"status":"ok"}`.
3. Try **Add data** (e.g. add a haulier), then **Find backhaul** to confirm the database and matching work.

---

## After deploy: what to send to companies

- **Web console:**  
  `https://yourapp.up.railway.app`  
  (or your custom domain if you add one.)

- **Find backhaul (pre-filled):**  
  `https://yourapp.up.railway.app/find-backhaul?vehicle_id=1&origin_postcode=B213NQ`

- **API docs:**  
  `https://yourapp.up.railway.app/docs`

Do **not** send `http://127.0.0.1:8000/...`; that only works on your computer.

---

## Optional: Custom domain (e.g. app.platoonix.co.uk)

1. In Railway, open your app service → **Settings** → **Domains**.
2. Add your domain (e.g. `app.platoonix.co.uk`).
3. In your domain registrar’s DNS, add the CNAME or A record Railway shows.
4. After DNS propagates, Railway will serve the app over HTTPS on that domain.

---

## If something fails

- **Build fails:** Check the build logs. Often it’s a missing dependency in `requirements.txt` or wrong Python version (we have `runtime.txt` for 3.11).
- **App starts then crashes:** Check the run logs. Often `DATABASE_URL` is wrong (missing `+psycopg2`, or wrong password/host).
- **“Application failed to respond”:** The app may be listening on the wrong port. Use start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- **Tables missing:** Run the one-off table-creation command from Step 6 against the same `DATABASE_URL` the app uses.

You’re ready to deploy when the checklist in Part 1 is done; then follow Part 2 step by step.
