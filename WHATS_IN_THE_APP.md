# What’s in the app (current setup)

Short list of what’s built and where to use it. No code or config to change unless we add something new.

---

## Auth & roles

- **Login / logout** – Login page; “Switch user / Sign out” in the header for admin, haulier, loader.
- **Register** – Haulier or loader can self‑register (company + email + password).
- **Forgot password** – Reset link shown on screen (no email needed).
- **Admin** – Full console (Vehicles, Loads, Routes, Matches, Create logins, Track). Default login: admin@platoonix.local / change-me (override with ADMIN_EMAIL / ADMIN_PASSWORD on Render).
- **Haulier** – Dashboard, vehicles, routes, find backhaul, **Driver** page, backhaul jobs.
- **Loader** – Dashboard, my loads, planned loads, Who’s interested (accept → job), Active jobs, Track.

---

## Matching & jobs

- **25‑mile radius** – Loads are matched to vehicles (or routes) when the load’s **pickup** is within 25 miles of the vehicle’s **base postcode** or the route’s **empty-at postcode**.
- **Auto‑match** – New load → suggested matches for vehicles with base postcode (and planned routes). New vehicle/route → suggested matches for open loads. Shown in **Matches** (admin) and **Who’s interested** (loader).
- **Interest → job** – Haulier (or admin) shows interest; loader accepts in **Who’s interested** → backhaul job is created.
- **Route-home matching (new)** – From **delivery** back to **base**: the app finds open loads whose pickup is within **25 miles of the whole route** (e.g. Manchester → Milton Keynes).  
  - **Driver page:** section “Loads on your route home” (needs vehicle **base postcode**).  
  - **Haulier → Find backhaul:** “From” and “To” postcodes + vehicle → **Loads along route**.

---

## Driver-led flow (no second person)

- **Driver page** (haulier: **Driver** in header): active job, status steps, share live location, upload ePOD, **Loads on your route home**.
- **Status steps:** Reached collection → Collected (pay captured) → Departed → Reached delivery → Upload ePOD (job done, payout).
- **Live GPS** – “Share live location” on Driver page; admin/loader see position on **Track** page.
- **Track page** – Admin: Matches → **Track**. Loader: Active jobs → **Track**. Haulier: Driver page → Track link. Shows status timeline and map (driver position when shared).
- **ePOD** – Driver: **Upload ePOD** → file + optional notes → job completed, payout released (Stripe if configured).

---

## Payments

- **RESERVED** when job is created → **CAPTURED** when driver taps **Collected** → **PAID_OUT** when delivery is confirmed (ePOD). Optional Stripe Connect for haulier payouts (set STRIPE_SECRET_KEY on Render).

---

## Config (already set)

- **Database** – PostgreSQL (DATABASE_URL on Render).
- **Session** – SESSION_SECRET_KEY on Render (required).
- **Platform fee** – PLATFORM_FEE_PERCENT (e.g. 0 for pilot).
- **Matching radius** – 25 miles (default_backhaul_radius_miles in code; no env var).
- **Optional** – ADMIN_EMAIL, ADMIN_PASSWORD, STRIPE_SECRET_KEY, SMTP_* on Render.

---

## Where things are (no Terminal for daily use)

| Feature              | Where |
|----------------------|--------|
| Driver page          | Haulier → **Driver** in header |
| Loads on route home  | Driver page (if vehicle has base postcode); or Haulier → Find backhaul → From / To → Loads along route |
| Track (live map)     | Admin: Matches → **Track**; Loader: Active jobs → **Track**; or Driver page link |
| Show interest        | Admin: Matches → **Interest**; or Haulier: Find backhaul (single postcode or route) then use Matches |
| Accept interest      | Loader: **Who’s interested** → **Accept** |

Nothing in the app is “half set” – the above is what’s implemented. To get the latest code (including route-home) on Render, push to GitHub once (see WHAT_YOU_NEED_TO_DO.md).
