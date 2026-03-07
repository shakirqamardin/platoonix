# Platoonix – user guide

## Log out / switch user

- **Admin console, Haulier dashboard, Loader dashboard:** You’ll see **“Logged in as your@email.com”** in the header so you know which account is active.
- Click **“Switch user / Sign out”** (or open **/logout** in the browser). You’ll be logged out and sent to the login page with the message “You are logged out. Log in with a different account below.”
- Log in with the other account’s email and password. Each browser session is one user; switching user clears the session.

If it ever looks like you’re still on the wrong account, use **Switch user / Sign out** and log in again with the correct email.

---

## Bulk upload loads (and matching)

1. Go to the **Loads** tab in the admin console.
2. Download the **CSV template** (link next to “Bulk upload loads”).
3. Open it in Excel or a text editor. The template has one example row; you can add more rows with:
   - `shipper_name`, `pickup_postcode`, `delivery_postcode` (required)
   - Optional: `pickup_window_start`, `pickup_window_end`, `weight_kg`, `volume_m3`, `budget_gbp`
4. Save as CSV (or .xlsx) and use **“Bulk upload loads”** to upload the file.
5. Each row creates one load. **Matching runs automatically for each new load**: vehicles with a **base postcode** (or planned routes) within ~25 miles of the load’s pickup get a **suggested** match.
6. After upload you’ll see “Uploaded N loads. Matching runs automatically — check the Matches tab.” Open the **Matches** tab to see suggested matches (collection → delivery).

To test matching with bulk upload: add some **vehicles with base postcodes** first (Vehicles tab), then bulk-upload loads whose pickup postcodes are within ~25 miles of those base postcodes. Suggested matches will appear in Matches.

---

## Quick flow

- **Add company** (Vehicles tab) → **Add vehicle** (with base postcode) → **Add load** (or bulk upload loads).
- **Matches** tab shows suggested loads (collection + delivery). Click **Interest** to express interest; the loader accepts in **Who’s interested** to create a job.
- **Backhaul jobs** (confirmed) show in the same tab; cancel a job there if you need to delete the load or vehicle.

---

## Driver-led flow (fully automated, no second person)

When a haulier is assigned a job, the **driver** controls the whole journey from their device. No dispatcher needed.

1. **Driver page** (haulier: click **Driver** in the header): shows your active job (pickup → delivery).  
2. **Share live location**: tap **Start** so admin and loader can see your position on the **Track** page.  
3. **Update status** (tap in order):  
   - **Reached collection** → **Collected** (this **captures payment**: RESERVED → CAPTURED)  
   - **Departed** → **Reached delivery**  
   - **Upload ePOD** → upload proof of delivery; this **completes the job and releases payout** to the haulier.  
4. **Track page**: admin and loader (and haulier) can open **Track** for a job to see live status and driver position on a map. From admin: **Matches** tab → **Track** next to the job. From loader: **Active jobs** → **Track**.

Payment is tied to these steps: reserved when the job is created, captured when the driver taps **Collected**, and paid out when the driver uploads ePOD and completes delivery.

---

## Loads on your route home (return to base)

When the driver is going **from the delivery location back to base**, the app finds open loads whose **pickup is within 25 miles of the whole route** (not just one point). For example: Manchester (delivery) → Milton Keynes (base) shows jobs anywhere along that corridor.

- **Driver page:** If the vehicle has a **base postcode** set, you see a **“Loads on your route home”** section: open loads along delivery → base. Tap **Interest** to open Find backhaul and express interest.
- **Haulier dashboard → Find backhaul:** Use **From** (e.g. delivery postcode) and **To** (e.g. base postcode), choose vehicle, then **Loads along route**. Same 25‑mile corridor; you can then show interest via the admin Matches tab or by using the single-postcode search with the load’s pickup.
