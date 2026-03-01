# How to build and run this platform (reliable, automated, future-proof)

## Recommended way to run it (no Nginx required)

- **Host:** Use a **managed PaaS** (Railway, Render, Fly.io, etc.). They give you HTTPS, a URL, and usually don’t buffer responses, so **SSE (live alerts) works** without extra config.
- **Database:** Use **managed PostgreSQL** from the same provider or Supabase/Neon. Set `DATABASE_URL` in the host’s environment.
- **Domain:** Point your domain at the host via **A record** or **CNAME** in your registrar’s DNS. The host then serves your app (and HTTPS). You do **not** need Nginx or Caddy unless you self-host on a VPS; for PaaS, the host is the reverse proxy and SSE is supported.

So: **one codebase → PaaS + managed Postgres + optional custom domain**. That’s the most efficient and reliable setup for going live.

---

## Fully automated flow (loaders + hauliers + your 8% fee)

The system is designed so that **loader and haulier availability** drive matching, and **collection** and **ePOD** drive payments, with your fee taken automatically.

### 1. Availability and matching (automated)

- **Loaders** post loads (one-off or planned weekly/monthly routes).
- **Hauliers** add vehicles and, optionally, planned empty legs (weekly/monthly).
- The **system** matches automatically (25 miles, vehicle/trailer, capacity) and can alert hauliers in real time (SSE) or suggest “show interest” for planned routes.
- No manual search needed: matches and suggestions appear from availability alone.

### 2. Assign job (fix price and reserve payment)

- When a load is assigned to a vehicle, the **job value** (`amount_gbp`) is set (e.g. from load budget or agreed price).
- The platform **fee** is applied automatically: **one 8% of the job value**, deducted from what the haulier receives (configurable via `PLATFORM_FEE_PERCENT`, default 8).
- Payment is created in status **RESERVED** (amount = job value, fee = 8%, net_payout = 92% to haulier).

### 3. Collection → payment collected (loader charged)

- When **collection** happens (or job start), call **`POST /api/payments/{payment_id}/collect`**.
- Payment moves to **CAPTURED**: money is considered collected from the loader/shipper.
- In production this would be triggered by your **payment provider** (e.g. Stripe capture) when you charge the loader; the API call keeps the app state in sync.

### 4. Delivery ePOD → payment made (haulier paid, your 8% kept)

- When the haulier uploads **ePOD** and it’s confirmed, call **`POST /api/pods/{pod_id}/confirm`**.
- The related payment moves to **PAID_OUT**: the haulier is paid **net_payout_gbp** (92%); the platform has already kept **fee_gbp** (8%).
- So: **ePOD confirm = trigger to pay the haulier**; the 8% fee is already accounted for and stays with the platform.

End-to-end: **availability → match → assign (8% fee set) → collection (payment collected) → ePOD confirm (haulier paid, you keep 8%)**. All trigger-based and automatable.

---

## Pilot then go live (recommended)

- **Month 1 – pilot:** Set **`PLATFORM_FEE_PERCENT=0`** (or a low value, e.g. 2). Run with real loaders and hauliers; no (or low) fee so adoption is easier and you can fix issues.
- **After pilot:** Set **`PLATFORM_FEE_PERCENT=8`**. No code change; only config. The UI shows the current fee so everyone sees 0% during pilot and 8% when live.

## 8% fee: one side only (recommended)

- **Recommended:** Take **one 8% of the job value**, deducted from the haulier’s payout.
  - Loader pays **amount_gbp** (100%).
  - Platform keeps **fee_gbp** = 8% of amount.
  - Haulier receives **net_payout_gbp** = 92% of amount.
- **Why not 8% from both?** Charging 8% from loader and 8% from haulier is effectively 16% of the deal and can feel heavy. One clear 8% (from the job value) is simpler, transparent, and easier to automate. You can still present it as “loader pays X, haulier receives Y after platform fee” in contracts or UI.
- Fee is configurable: set **`PLATFORM_FEE_PERCENT`** in the environment (default 8). On **assign**, if you don’t pass `fee_gbp`, it is computed as `amount_gbp * (PLATFORM_FEE_PERCENT / 100)`; otherwise you can override per job.

---

## Payment status flow (summary)

| Step              | Trigger                    | Payment status | Meaning                          |
|-------------------|----------------------------|----------------|----------------------------------|
| Assign job        | `POST /api/matches/assign` | RESERVED       | Job value set; 8% fee computed   |
| Collection        | `POST /api/payments/{id}/collect` | CAPTURED  | Payment collected from loader    |
| ePOD confirmed    | `POST /api/pods/{id}/confirm`    | PAID_OUT   | Haulier paid (net); you keep fee |

---

## Domain and SSE (recap)

- **PaaS:** Point your domain at the host (A/CNAME). Host provides HTTPS. No Nginx/Caddy needed; SSE works if the host doesn’t buffer (Railway, Render, Fly typically don’t).
- **Self-hosted (VPS + Nginx/Caddy):** If you do use a reverse proxy, disable buffering for `/api/alerts/stream` so Server-Sent Events reach the browser immediately.
