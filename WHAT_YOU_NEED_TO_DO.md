# What you need to do (no coding – just these steps)

After the last update (driver in control, live GPS, status steps, ePOD, payment), here is **everything** you need to do. You do **not** need to change any code or the `.env` file for this update.

---

## 1. Get the new version onto your live site (if you use Render)

The new features are already in your project folder. For your **live** site (e.g. platoon-ix.onrender.com) to have them, Render must get the latest code from GitHub.

**Do this once:**

1. On your Mac, open **Terminal** (press **⌘ + Space**, type **Terminal**, press **Enter**).
2. Copy the first line below, paste it into Terminal, press **Enter**:

```bash
cd /Users/mac/Desktop/Platoonixcursor
```

3. Copy the next three lines **one by one**, paste, press **Enter** after each:

```bash
git add .
```

```bash
git commit -m "Driver flow, live GPS, track, ePOD"
```

```bash
git push
```

4. If **git push** asks for your GitHub username and password, use your GitHub username and a **Personal Access Token** (not your normal password). If you haven’t created a token: GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Generate new token** → tick **repo** → generate → copy the token and paste it when Terminal asks for the password.

5. Go to **Render** (dashboard.render.com) → your **platoonix** service. Render will redeploy automatically. Wait until it says the deploy is live (usually 2–5 minutes).

After this, your live site has the driver page, live GPS, track page, and ePOD flow. You don’t need to do this again unless you’re told there’s another update to push.

---

## 2. You do NOT need to change these

- **`.env` file** – No changes needed for the driver/GPS/ePOD update. Leave it as it is.
- **Render Environment variables** – No new variables. Keep **DATABASE_URL**, **SESSION_SECRET_KEY**, **PLATFORM_FEE_PERCENT** (and **ADMIN_EMAIL** / **ADMIN_PASSWORD** if you set them) as they are.
- **Any code or config** – Nothing to edit. All updates are already in the project.

---

## 3. Where to use the new features (no Terminal, just the website)

Everything happens in the browser on your live URL (e.g. **https://platoon-ix.onrender.com**).

| What | Where |
|------|--------|
| **Driver page** (job, status buttons, share location, upload ePOD) | Log in as **haulier** → click **Driver** in the header. |
| **Track page** (live status + map) | **Admin:** Matches tab → **Track** next to a job. **Loader:** Active jobs → **Track**. **Haulier:** Driver page → **Track job** link. |
| **Live GPS** | On the Driver page, click **Start** under “Share live location”. Others see your position on the Track page. |
| **Payment** | Runs automatically: **Collected** = pay captured; **Upload ePOD & complete delivery** = job done and payout released (if Stripe is set). |

You don’t need to run anything in Terminal for normal use. Only the **git** steps in section 1 are needed once to give Render the new code.

---

## 4. If you only run the app on your Mac (no Render)

1. In Terminal:

```bash
cd /Users/mac/Desktop/Platoonixcursor
```

2. Start (or restart) the app:

```bash
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

(If you don’t use `.venv`, use: `python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.)

3. Open **http://127.0.0.1:8000** in your browser. The new driver and track features are there; no other updates needed.

---

## Summary

| Action | Where | Do you need to do it? |
|--------|--------|------------------------|
| Push code to GitHub (git add, commit, push) | Terminal | **Yes, once**, if your app is on Render. |
| Change `.env` | Your project folder | **No.** |
| Add/change env vars on Render | Render dashboard | **No** for this update. |
| Use Driver / Track / ePOD | Browser on your live URL | **Yes**, whenever the driver or admin/loader use the app. |
| Run or restart app locally | Terminal | Only if you run the app on your Mac. |

If you only do the **git** steps in section 1 (when using Render), your live app will have the fully automated driver flow, live GPS for all parties, and payment tied to collection and delivery. Nothing else is required from you.
