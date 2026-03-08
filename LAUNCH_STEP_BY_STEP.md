# Launch Platoonix – step by step

Do **one step at a time**. Finish each step before going to the next.

---

## PART A: Put your code on GitHub

### Step 1 – Open GitHub and create a repository

1. In your browser go to **https://github.com** and log in (or sign up).
2. Click the **+** at the top right → **New repository**.
3. **Repository name:** type **platoonix**.
4. Leave everything else as it is. Click **Create repository**.
5. Leave this page open. You will need your GitHub **username** later (it’s in the top right or in the repo URL).

---

### Step 2 – Open Terminal on your Mac

1. Press **⌘ + Space**.
2. Type **Terminal** and press **Enter**.

---

### Step 3 – Go to your project folder

Copy this line, paste it into Terminal, then press **Enter**:

```
cd /Users/mac/Desktop/Platoonixcursor
```

---

### Step 4 – Connect the folder to Git and GitHub

Run these **one at a time**. After each line, press **Enter**.

**4a – Start Git in this folder**

```
git init
```

**4b – Point to your GitHub repo**  
Replace **YOUR-GITHUB-USERNAME** with your real GitHub username:

```
git remote add origin https://github.com/YOUR-GITHUB-USERNAME/platoonix.git
```

Example: if your username is **johndoe**, the line is:

```
git remote add origin https://github.com/johndoe/platoonix.git
```

---

### Step 5 – Push your code to GitHub

Run these **one at a time**:

```
git add .
```

```
git commit -m "Initial commit"
```

```
git branch -M main
```

```
git push -u origin main
```

- If it asks for your **username**: type your GitHub username and Enter.
- If it asks for a **password**: GitHub no longer accepts your normal password. You must use a **Personal Access Token**:
  1. On GitHub go to **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**.
  2. **Generate new token (classic)**. Give it a name (e.g. “Platoonix”), tick **repo**, then **Generate token**.
  3. **Copy** the token (you won’t see it again). When Terminal asks for a password, **paste this token** and press Enter.

When Step 5 is done, open **https://github.com/YOUR-USERNAME/platoonix** in your browser. You should see your project files.

---

## PART B: Deploy on Render (so the app is live on the internet)

### Step 6 – Create a Render account and add a database

1. Go to **https://render.com** in your browser.
2. Click **Get Started** or **Log in** and sign in with **GitHub** (so Render can see your repos).
3. In the Render dashboard click **New +** → **PostgreSQL**.
4. **Name:** e.g. **platoonix-db**. **Region:** choose one near you. Click **Create Database**.
5. Wait until the database shows **Available** (green). This can take 1–2 minutes.

---

### Step 7 – Copy the database URL and fix it for the app

1. Click your new database (**platoonix-db**).
2. Under **Connections** you’ll see **Internal Database URL**. Click **Copy** (copy the whole URL).
3. Open **Notes** or **TextEdit** and paste it. It will look like:  
   `postgresql://user:password@hostname/database`
4. **Change the start** of the URL:  
   - Find: `postgresql://`  
   - Replace with: `postgresql+psycopg2://`  
   So the full URL starts with **postgresql+psycopg2://** and the rest stays the same.
5. Copy this **full** URL again. You will use it in Step 10.  
   Example: `postgresql+psycopg2://user:password@hostname/database`

---

### Step 8 – Create the web service (your app) on Render

1. In Render, click **New +** → **Web Service**.
2. Under **Connect a repository** click **Build and deploy from a Git repository**.
3. If asked, connect your GitHub account and allow Render to see your repos.
4. Find **platoonix** in the list and click **Connect** next to it.
5. Use these settings:
   - **Name:** **platoonix** (or leave as suggested).
   - **Region:** same as your database.
   - **Branch:** **main**.
   - **Runtime:** **Python 3**.
   - **Build Command:**  
     `pip install -r requirements.txt`
   - **Start Command:**  
     `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Click **Create Web Service**. Render will start building. Wait until the status says **Live** (often 3–5 minutes).

---

### Step 9 – Create a secret key for sessions

1. Open **Terminal** on your Mac.
2. Run:

```
openssl rand -hex 32
```

3. Copy the long string that appears (letters and numbers). You will paste it in Step 10.

---

### Step 10 – Add environment variables on Render

1. In Render, open your **platoonix** **web service** (not the database).
2. In the left menu click **Environment**.
3. Click **Add Environment Variable** and add these **one by one**:

| Key | Value |
|-----|--------|
| **DATABASE_URL** | The URL you saved in Step 7 (the one starting with `postgresql+psycopg2://`). |
| **SESSION_SECRET_KEY** | The long string you copied in Step 9. |
| **PLATFORM_FEE_PERCENT** | `0` |

4. (Recommended) Add two more so your live site doesn’t use the default admin password:

| Key | Value |
|-----|--------|
| **ADMIN_EMAIL** | Your real email (e.g. the one you use for work). |
| **ADMIN_PASSWORD** | A strong password you choose for logging in as admin on the live site. |

5. After you add or change variables, Render will **redeploy** automatically. Wait until the service is **Live** again.

---

### Step 11 – Get your live URL

1. Still in your **platoonix** web service on Render.
2. At the top you’ll see **Your service is live at** followed by a link, e.g. **https://platoonix-xxxx.onrender.com**.
3. Click that link or copy the URL.

---

### Step 12 – Open your app in the browser

1. Open a new browser tab.
2. Paste the URL you copied (e.g. **https://platoonix-xxxx.onrender.com**).
3. Press **Enter**.

You should see the **Platoonix login page**.

---

### Step 13 – Log in and do a quick test

1. **Email:**  
   - If you set **ADMIN_EMAIL** in Step 10, use that.  
   - Otherwise use: **admin@platoonix.local**
2. **Password:**  
   - If you set **ADMIN_PASSWORD** in Step 10, use that.  
   - Otherwise use: **change-me**
3. Click **Log in**.
4. You should see the main Platoonix screen with tabs (Find backhaul, Vehicles, Loads, etc.).
5. Click **Vehicles** → add one company (name, email, phone) → **Add company**. If the company appears in the list, the app and database are working.

---

## You’re done

Your app is **live**. Share the URL from Step 11 (e.g. **https://platoonix-xxxx.onrender.com**) with anyone who should use it. They open it in their browser and log in (you create their accounts in the **Admin** tab).

- **On a phone:** Open that same URL in Safari or Chrome on the phone. You can also use **Add to Home Screen** so it opens like an app.

---

## If something goes wrong

- **“Application failed to respond” or blank page**  
  Wait 2–3 minutes after the first deploy or after changing env vars, then try again. Check that **Start Command** in Step 8 is exactly:  
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

- **Build failed**  
  In Render click your **platoonix** service → **Logs**. Read the red error. Often it’s a typo in an environment variable name (e.g. **DATABASE_URL** not **DATABASE_URLS**).

- **Can’t push to GitHub (password)**  
  Use a **Personal Access Token** as the password (see the note in Step 5).

- **Database or “connection” error**  
  Check that **DATABASE_URL** in Step 10 starts with **postgresql+psycopg2://** (not just `postgresql://`).

If you tell me the exact step number and the message you see, I can tell you the next move in plain words.
