# What you need to do – simple steps (no coding)

Do these in order. You don’t need to change any code or the `.env` file.

---

## 1. Base postcode (company = standard; driver can override)

- **Company base (standard):** When you’re logged in as **Haulier**, open **My company** and fill in **Base postcode** (e.g. Milton Keynes). Save. That’s the default “return to” for route home for all your vehicles.
- **Per vehicle (optional):** In **My vehicles** you can set a **Base postcode** for a single vehicle. That overrides the company base for that vehicle.
- **Driver override:** On the **Driver** page, the driver can type a different “return to” postcode in **Override return to** and click **Update** for that trip only.

So: **base is set in the company profile (login) as standard**, and the driver can override when needed.

---

## 2. Get the latest app onto your live site (Render)

Do this **once** so the live site has the new features (company base, route home, etc.).

### On your Mac

1. Press **⌘ + Space**, type **Terminal**, press **Enter**.
2. Copy this line, paste into Terminal, press **Enter**:
   ```
   cd /Users/mac/Desktop/Platoonixcursor
   ```
3. Copy this line, paste, press **Enter**:
   ```
   git add .
   ```
4. Copy this line, paste, press **Enter**:
   ```
   git commit -m "Company base postcode and route home"
   ```
5. Copy this line, paste, press **Enter**:
   ```
   git push origin main
   ```
   - If it says “rejected” or “pull first”, tell me and we’ll fix it.
   - If it asks for a password, use your **GitHub Personal Access Token**, not your normal password.

### On Render

6. Open **dashboard.render.com** and your **platoonix** (or platoon-ix) service.
7. Wait 2–5 minutes. Render will redeploy on its own when it sees the new push.
8. When it says the deploy is live, your site has the update.

You don’t need to run anything else in Terminal for normal use.

---

## 3. Where to set things (in the website, not Terminal)

| What | Where |
|------|--------|
| **Company base postcode** (standard for route home) | Log in as **Haulier** → **My company** → **Base postcode** → **Save company details**. |
| **Vehicle base** (overrides company for that vehicle) | Haulier → **My vehicles** → when adding/editing a vehicle, **Base postcode (for auto-match)**. |
| **Driver override** (for one trip) | **Driver** page → **Override return to** → type postcode → **Update**. |
| **Driver page** | Log in as Haulier → click **Driver** in the header. |
| **Loads on route home** | Shown on the Driver page when company or vehicle has a base (or driver has overridden). |

---

## 4. Summary

- **Base:** Set once in **Haulier → My company** (Base postcode). That’s the standard. Set per vehicle if you want; driver can override on the Driver page.
- **Terminal:** Only for pushing the latest code to GitHub (steps 1–5 above), so Render can deploy. No other Terminal steps needed for you.
- **Other places:** Nothing to update in `.env` or in Render environment variables for this. Just use the website as above.

If any step doesn’t work (e.g. `git push` fails), say which step and what you see and we’ll fix it.
