# Platoonix – step-by-step instructions

Do each step in order. Use a new line for each command or action.

---

## PART 1: Get the app live (if not already)

### 1.1 Put your code on GitHub

1. Open **https://github.com** and log in (or create an account).
2. Click **+** (top right) → **New repository**.
3. Repository name: **platoonix** (or any name). Leave the rest default. Click **Create repository**.
4. On your Mac, open **Terminal** (⌘ + Space, type Terminal, Enter).
5. Run these commands **one at a time** (paste, then Enter after each):

```bash
cd /Users/mac/Desktop/Platoonixcursor
```

```bash
git init
```

```bash
git remote add origin https://github.com/YOUR-USERNAME/platoonix.git
```
(Replace **YOUR-USERNAME** with your real GitHub username.)

```bash
git add .
```

```bash
git commit -m "Initial commit"
```

```bash
git branch -M main
```

```bash
git push -u origin main
```
(If it asks for a password, use a **Personal Access Token** from GitHub, not your normal password.)

---

### 1.2 Deploy on Render (or keep existing deployment)

1. Go to **https://render.com** and log in (or use your existing account).
2. Open your **platoonix** web service (or create one: **New** → **Web Service** → connect **platoonix** repo).
3. Click your **platoonix** service → **Environment** (or **Environment** in the left menu).
4. Add these variables (click **Add variable** for each). Use the **exact** names:

| Name | Value |
|------|--------|
| **DATABASE_URL** | Your Postgres URL from Render (if you use Postgres, change the start to `postgresql+psycopg2://` if the app expects that). |
| **SESSION_SECRET_KEY** | A long random string. In Terminal run: `openssl rand -hex 32` and paste the result. |
| **PLATFORM_FEE_PERCENT** | `0` (for pilot). |

5. (Recommended) Add:

| Name | Value |
|------|--------|
| **ADMIN_EMAIL** | Your real admin email. |
| **ADMIN_PASSWORD** | A strong password (so production is not using the default). |

6. Save. Render will redeploy. Wait for the deploy to finish.
7. Copy your live URL (e.g. **https://platoon-ix.onrender.com**). That is the app others will use.

---

## PART 2: Use the app (admin, haulier, loader)

### 2.1 Log in

1. Open your live URL in a browser (e.g. **https://platoon-ix.onrender.com**).
2. Log in with:
   - **Admin:** the email and password you set with **ADMIN_EMAIL** and **ADMIN_PASSWORD** (or default: **admin@platoonix.local** / **change-me**).
   - **Haulier or Loader:** use an account you created via **Register** or that admin created in **Create logins**.

---

### 2.2 Create a backhaul job (admin or loader + haulier)

1. **Admin:** In the **Vehicles** tab → **Add company** (Company name, Email, Phone) → **Add company**.
2. **Admin:** In the same tab → choose that company in the dropdown → enter **Reg**, **Type**, **Trailer**, and **Base postcode** → **Add vehicle**.
3. **Admin:** In the **Loads** tab → **Add load** (Shipper, Pickup postcode, Delivery postcode) → **Add load** (pickup should be within about 25 miles of the vehicle’s base postcode for matching).
4. **Admin:** Open the **Matches** tab. You should see a **suggested match**. Click **Interest** (as if the haulier is expressing interest).
5. **Loader:** Log in as the loader who owns that load → **Who’s interested** → click **Accept** on that interest. That creates the **backhaul job**.
6. **Admin:** In **Matches**, the **Backhaul jobs** table now shows the new job. You can click **Track** to open the track page later.

---

## PART 3: Driver-led flow (driver in control, live GPS, payment)

### 3.1 Driver opens their job

1. Log in as the **haulier** (the company that has the job).
2. In the header, click **Driver**.
3. You see the **active job**: pickup postcode → delivery postcode and the status steps.

---

### 3.2 Share live location (optional)

1. On the Driver page, find **Share live location**.
2. Click **Start**. Allow location access if the browser asks.
3. Your position is sent to the server. Admin and loader can see it on the **Track** page.
4. Click **Stop** when you want to stop sharing.

---

### 3.3 Update status (do in order)

1. **Reached collection** – When you are at the pickup, click **I’m here** (Reached collection).
2. **Collected** – When the load is on the vehicle, click **Collected**. This **captures payment** (RESERVED → CAPTURED).
3. **Departed** – When you leave the pickup, click **Departed**.
4. **Reached delivery** – When you are at the delivery point, click **I’m here** (Reached delivery).
5. **Delivered + ePOD** – Click **Upload ePOD**:
   - You are taken to the ePOD page.
   - Choose a file (PDF, JPG, PNG, or HEIC, max 10 MB).
   - Optionally add notes.
   - Click **Upload & complete delivery**. This **completes the job and releases payout** to the haulier.

---

### 3.4 Track page (admin / loader / haulier)

1. **Admin:** **Matches** tab → next to the job, click **Track**.
2. **Loader:** **Active jobs** → click **Track** for the job.
3. **Haulier:** On the Driver page, use the **Track job** link for that job.
4. On the Track page you see:
   - Load (pickup → delivery).
   - Status (reached pickup, collected, departed, reached delivery, completed).
   - A **map** with the driver’s live position (if they have **Share live location** on). The page refreshes position every few seconds.

---

## PART 4: Quick checklist (fully operational)

- [ ] Code is on GitHub (Part 1.1).
- [ ] App is deployed on Render (or your host) and opens at your URL (Part 1.2).
- [ ] **DATABASE_URL**, **SESSION_SECRET_KEY**, **PLATFORM_FEE_PERCENT** are set (Part 1.2).
- [ ] **ADMIN_EMAIL** and **ADMIN_PASSWORD** are set (Part 1.2).
- [ ] You can log in as admin and see the console (Part 2.1).
- [ ] You have run one full flow: company → vehicle (with base postcode) → load → Matches → Interest → Loader accepts → job appears (Part 2.2).
- [ ] As haulier you can open **Driver**, see the job, tap status steps, share location, and upload ePOD (Part 3).
- [ ] Admin or loader can open **Track** and see status and (when shared) live driver position (Part 3.4).

When all boxes are ticked, the app is fully operational and you can share the URL with users.
