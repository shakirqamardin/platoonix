# Platoonix – fully operational checklist

Use this after the app is deployed (e.g. on Render) to get it ready for real users.

---

## Already built (no code changes needed)

- **Auth:** Login, logout, register (haulier / loader), forgot password (reset link shown on screen; no email required).
- **Admin console:** Vehicles (companies + vehicles, base postcode for auto-match), Loads (add + bulk upload), Routes (haulier routes + planned loads), Matches (suggested → Interest → backhaul jobs), Create logins (haulier + loader), Cancel job.
- **Haulier:** Dashboard, vehicles, routes, find backhaul, show interest, backhaul jobs.
- **Loader:** Dashboard, my loads, **Who’s interested** (accept to create backhaul job).
- **Matching:** New load → suggested matches for vehicles with base postcode / routes; new vehicle or route → suggested matches for open loads.
- **Payments:** Reserved on job, capture on collection confirm, payout on delivery confirm (Stripe optional).
- **ePOD:** Upload, create POD, confirm collection, confirm delivery.
- **Deploy:** Runs on Render; tables created on startup; health check at `/health`.

---

## What you need to do (one-time)

### 1. Set production env vars on Render

In **Render** → your **platoonix** service → **Environment**:

| Variable | Required? | What to set |
|----------|-----------|-------------|
| `DATABASE_URL` | Yes | From Render Postgres (use `postgresql+psycopg2://...` if needed). |
| `SESSION_SECRET_KEY` | Yes | Long random string, e.g. run `openssl rand -hex 32` and paste. |
| `PLATFORM_FEE_PERCENT` | Yes | `0` for pilot (or e.g. `8`). |
| `ADMIN_EMAIL` | Recommended | Your real admin email (so you’re not using the default). |
| `ADMIN_PASSWORD` | Recommended | Strong password (replaces default `change-me`). |
| `STRIPE_SECRET_KEY` | Optional | Only if you use Stripe Connect for payouts. |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, etc. | Optional | Only if you want match/interest emails. |

If you don’t set `ADMIN_EMAIL` / `ADMIN_PASSWORD`, the app uses **admin@platoonix.local** / **change-me** — change them after first login or set env vars so production is secure.

### 2. Run one full flow (smoke test)

1. Open your live URL (e.g. **https://platoon-ix.onrender.com**).
2. Log in as admin (or create a haulier and a loader via Admin → Create logins, or Register).
3. **Admin:** Vehicles tab → Add company → Add vehicle (set **Base postcode**).
4. **Admin:** Loads tab → Add load (pickup postcode within ~25 miles of vehicle base).
5. **Admin:** Matches tab → you should see a suggested match → click **Interest** (as haulier or use find-backhaul as haulier).
6. **Loader:** Log in as loader → **Who’s interested** → **Accept** on that interest.
7. **Admin:** Matches tab → Backhaul jobs table should show the new job with payment (e.g. RESERVED).

If that works end-to-end, the app is fully operational for core use.

### 3. Optional later

- **Custom domain:** When you buy a domain (e.g. platoonix.com), add it in Render → Settings → Custom Domains and point DNS as shown.
- **Email:** Add SMTP env vars if you want match/interest emails; otherwise matching and jobs still work, users just log in to see updates.
- **Stripe:** Add `STRIPE_SECRET_KEY` (and haulier `payment_account_id`) when you’re ready for real payouts.

---

## Summary

- **Code:** Feature-complete for core backhaul flow (match → interest → accept → job → collection → delivery → payout).
- **Your tasks:** Set env vars on Render (especially `SESSION_SECRET_KEY` and, ideally, `ADMIN_EMAIL` / `ADMIN_PASSWORD`), then run the smoke test above. After that, you can share the live URL and use it with real users; add domain and email when you’re ready.
