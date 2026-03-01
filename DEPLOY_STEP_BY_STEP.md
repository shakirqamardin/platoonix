# Deploy your app – step by step (no coding needed)

Do **each step in order**. Pause after each step until it’s done.

---

## BEFORE YOU START

- Your app runs on your Mac (you can open http://127.0.0.1:8000/ and see the console).
- You have the project folder open (e.g. in Cursor): **Platoonixcursor**.

---

# PART A: Put your code on GitHub

(GitHub is where your code lives online so Railway can use it.)

---

## Step 1: Create a GitHub account (if you don’t have one)

1. Open your browser and go to: **https://github.com**
2. Click **Sign up**.
3. Enter email, password, and a username. Complete the sign-up.
4. You don’t need to create any repository yet – just have the account.

---

## Step 2: Create a new repository on GitHub

1. Log in to GitHub.
2. Click the **+** at the top right → **New repository**.
3. **Repository name:** type **platoonix** (or any name you like).
4. Leave everything else as default (Public, no README).
5. Click **Create repository**.
6. Leave this tab open. You’ll see a page that says “Quick setup” or “…or push an existing repository from the command line”.

---

## Step 3: Open Terminal on your Mac

1. Press **Command (⌘) + Space**.
2. Type **Terminal** and press **Enter**.

---

## Step 4: Go to your project folder in Terminal

1. Copy this line (everything on one line):

```
cd /Users/mac/Desktop/Platoonixcursor
```

2. Click in the Terminal window and paste (**Command + V**).
3. Press **Enter**.

---

## Step 5: Turn on Git in your project (if it’s not already)

Run these **one at a time** (copy the line, paste in Terminal, press Enter):

**5a**

```
git init
```

**5b** (replace **YOUR-GITHUB-USERNAME** with your GitHub username and **platoonix** with your repo name if different):

```
git remote add origin https://github.com/YOUR-GITHUB-USERNAME/platoonix.git
```

Example: if your username is **johndoe**, the line is:

```
git remote add origin https://github.com/johndoe/platoonix.git
```

---

## Step 6: Add all files and push to GitHub

**6a – Add files**

```
git add .
```

**6b – First save (commit)**

```
git commit -m "Initial commit"
```

**6c – Push to GitHub** (this may ask for your GitHub username and password; use a **Personal Access Token** if it says “password not supported” – see note below)

```
git push -u origin main
```

If it says **branch 'main' doesn't exist**, try:

```
git branch -M main
git push -u origin main
```

**Note:** If GitHub asks for a password, you must use a **Personal Access Token**, not your normal password. On GitHub: **Settings** → **Developer settings** → **Personal access tokens** → **Generate new token**. Give it a name, tick **repo**, generate, then copy the token and paste it when Terminal asks for a password.

---

When Step 6 is done, your code is on GitHub. Go to **https://github.com/YOUR-USERNAME/platoonix** and you should see your project files.

---

# PART B: Deploy on Railway (so the app has a public URL)

---

## Step 7: Create a Railway account and log in

1. Go to **https://railway.app** in your browser.
2. Click **Login** or **Start a new project**.
3. Choose **Login with GitHub**.
4. Approve Railway so it can use your GitHub account. You’re in the Railway dashboard.

---

## Step 8: Start a new project and add a database

1. Click **New Project**.
2. You’ll see something like “Deploy from GitHub” or “Empty project”.  
   Click **Empty project** (or **New**) so you have a blank project.
3. Inside the project, click **+ New** (or **Add service**).
4. Choose **Database** → **PostgreSQL** (or **Add PostgreSQL**).
5. Wait until it says the database is running (green or “Active”). This can take a minute.

---

## Step 9: Get your database connection URL

1. Click the **Postgres** box (the database you just added).
2. Open the **Variables** or **Connect** or **Data** tab.
3. Find **DATABASE_URL** or **Postgres Connection URL**. It looks like:
   `postgresql://postgres:longpassword@containers-us-west-123.railway.app:6543/railway`
4. Click **Copy** (or select and copy) that full URL.
5. Open **Notes** or **TextEdit** on your Mac and paste it there.
6. **Change it for the app:** in that URL, find the first `postgresql://` and change it to:
   `postgresql+psycopg2://`
   So the start becomes: **postgresql+psycopg2://** (rest stays the same).
7. Copy this **new** URL. You’ll need it in Step 12. Example:
   `postgresql+psycopg2://postgres:longpassword@containers-us-west-123.railway.app:6543/railway`

---

## Step 10: Add your app from GitHub

1. In the **same** Railway project, click **+ New** again (or **Add service**).
2. Choose **GitHub Repo** (or **Deploy from GitHub**).
3. If asked, click **Configure GitHub App** and allow Railway to see your repositories.
4. Select the repo you created (e.g. **platoonix**). Click it.
5. Railway will create a “service” for your app and start building. Wait for the build to finish (you may see “Building…” then “Deploying…”). This can take a few minutes.

---

## Step 11: Open your app’s settings

1. In the project, click the **service that is your app** (the one with your repo name, e.g. **platoonix**), **not** the Postgres service.
2. Click **Variables** (or **Settings** → **Variables**).

---

## Step 12: Add DATABASE_URL for the app

1. Under **Variables**, click **+ New Variable** or **Add variable**.
2. **Variable name:** type exactly: **DATABASE_URL**
3. **Value:** paste the URL you saved in Step 9 (the one that starts with **postgresql+psycopg2://**).
4. Save (Enter or **Add**). Railway will redeploy the app.

---

## Step 13: Add PLATFORM_FEE_PERCENT (for pilot)

1. In the same **Variables** section, click **+ New Variable** again.
2. **Variable name:** **PLATFORM_FEE_PERCENT**
3. **Value:** **0**
4. Save.

---

## Step 14: Set the start command (so the app runs correctly)

1. Click your **app service** (the one with your repo name).
2. Go to **Settings** (gear icon or **Settings** tab).
3. Find **Start Command** or **Deploy** or **Build & Deploy**.
4. If there’s a box for **Start Command** or **Custom start command**, type exactly:

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

5. Save. Railway may redeploy again.

(If you don’t see a start command box, skip this step; Railway might already use the Procfile in your project.)

---

## Step 15: Create the database tables (one-time)

1. In Railway, click your **app service**.
2. Find **Settings** or **Deploy** and look for **Run Command** or **One-off command** or **Shell**. (Some plans have “Run” or “Execute command”.)
3. If you see a way to run a command, run this **one line** (copy all of it):

```
python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
```

4. If Railway doesn’t have “run command”, use **Railway CLI**:
   - Go to **https://docs.railway.app/develop/cli** and install the CLI (copy the install command they give).
   - In Terminal: `cd /Users/mac/Desktop/Platoonixcursor`, then `railway login`, then `railway link` (choose your project and app), then run:
   ```
   railway run python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
   ```

When this runs without errors, your tables are created.

---

## Step 16: Get your public website URL

1. Click your **app service** (not Postgres).
2. Open the **Settings** or **Networking** or **Deploy** tab.
3. Find **Generate domain** or **Public networking** or **Domain**.
4. Click **Generate domain** (or **Add domain**). Railway will give you a URL like:
   **https://platoonix-production-xxxx.up.railway.app**
5. **Copy** that URL.

---

## Step 17: Open your live site in the browser

1. Open a new browser tab.
2. Paste the URL you copied (e.g. **https://platoonix-production-xxxx.up.railway.app**).
3. Press **Enter**.

You should see your **Backhaul Logistics Console** (same as on your Mac, but now on the internet).

---

## Step 18: Quick test

1. On the live site, try **Add data** → **Hauliers & vehicles** → add one haulier (name, email, phone) and click **Add haulier**.
2. If it saves and you see the haulier in the list, the app and database are working.

---

# DONE

Your app is deployed. You can send companies:

- **Main site:** your Railway URL (e.g. **https://platoonix-production-xxxx.up.railway.app**)
- **Find backhaul (example):** same URL + **/find-backhaul?vehicle_id=1&origin_postcode=B213NQ**

Do **not** send **http://127.0.0.1:8000** – that only works on your computer.

---

# If something goes wrong

- **“Application failed to respond” or blank page:** Wait 1–2 minutes after deploy, then try again. If it still fails, check Step 14 (start command) and Step 12 (DATABASE_URL with **postgresql+psycopg2://**).
- **Build failed:** In Railway, click your app service and open **Deployments** → click the latest one → read the **Build logs**. Often it’s a typo in a variable name (e.g. DATABASE_URL).
- **Tables missing / error when adding data:** Run Step 15 again (create tables) using the same DATABASE_URL the app uses.
- **Git push asked for password:** Use a GitHub **Personal Access Token** instead of your GitHub password (see note in Step 6).

If you tell me the exact message or step number where you’re stuck, I can give you the next move in plain words.
