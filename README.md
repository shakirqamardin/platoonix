 ## Backhaul Logistics Platform (Draft)

This project is a prototype for an automated backhaul logistics platform for road hauliers and distributors.

### Tech stack

- **Backend**: Python, FastAPI
- **Database**: PostgreSQL

### High-level features

- Haulier and vehicle onboarding, including vehicle + trailer capabilities.
- Distributor load posting with pickup/delivery locations and constraints.
- Automated backhaul matching within a configurable radius (default 25 miles).
- ULEZ/CAZ-aware matching using DVLA Vehicle Enquiry API data (to be wired).
- POD upload and confirmation to trigger immediate payment flows.

### How it works in real life (and in the UI)

- **One console, one page** – There is no separate “tab” for 25-mile matching. The same page has a **“Find backhaul for a vehicle”** section: you pick a **vehicle** (from a dropdown by registration and trailer type) and enter **your location now** (UK postcode). That runs the 25-mile + vehicle/trailer matching and shows suitable loads.
- **Who sees what** – **Hauliers** look for backhaul: they choose “this truck” and “I’m at this postcode” and get a list of open loads within 25 miles that match that vehicle’s trailer type and capacity. **Loaders/shippers** (distributors) post loads; they don’t need to know which vehicle will take it – matching is the other way round (vehicle + location → loads).
- **How hauliers know the vehicle** – They don’t need to know a “Vehicle ID”. In the UI they pick from a **dropdown of vehicles** (e.g. “AB12 CDE · artic · curtain_sider”). That dropdown is built from all vehicles in the system; with login later, it would show only that haulier’s trucks.

### Real-time alerts (less manual work)

Hauliers can get **live alerts** when a new load is posted that matches their vehicle and location, instead of manually clicking “Find loads”:

- In the **Find backhaul** section, click **“Start live alerts”** (after choosing vehicle and postcode). The page keeps a connection open and shows new matching loads as they appear.
- Alerts are sent over **Server-Sent Events** (`GET /api/alerts/stream?vehicle_id=…&origin_postcode=…`). When a load is created (via API or bulk upload), the server checks all active subscribers and pushes an event to those whose vehicle + postcode match the new load (same 25-mile and trailer/capacity rules).

So: distributors post loads as usual; hauliers who have “Start live alerts” on get notified automatically.

### Weekly/monthly routes and automatic “show interest” alerts

If **loaders** and **hauliers** both enter their **weekly or monthly routes**, the system can **automatically** match and **alert potential hauliers** so they can **show interest**:

- **Loaders** add **planned loads** (e.g. “Every Tuesday we need a collection from Manchester to Birmingham”). Use **Loader: add planned route** in the UI or `POST /api/planned-loads`.
- **Hauliers** add **empty legs** (e.g. “Every Tuesday I’m empty at Manchester with this vehicle”). Use **Haulier: add my route** in the UI or `POST /api/haulier-routes`.
- When either side adds or updates a route, **matching runs automatically**: same day of week, load pickup within 25 miles of the haulier’s “empty at” postcode, and vehicle/trailer/capacity match. For each match the system:
  - **Pushes a live alert** to any haulier who has “Start live alerts” open for that vehicle + postcode (SSE event type `planned_load_match`).
  - Creates a **suggested** **Load interest** so the haulier sees it in **Suggested for you** and can click **Show interest**. Loaders can then see who’s interested and assign the job.

So: both sides enter routes once; the system looks for matches and alerts hauliers to show interest, with less manual work.

For **automated payment flow** (collection → payment collected, ePOD → haulier paid, your 8% fee), see **docs/ARCHITECTURE.md**.

### Running the backend (dev)

**To see the website in your browser you must start the server.** Step-by-step (including where to change the platform fee %): see **RUN.md**.

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start a local PostgreSQL instance and create a database, e.g. `backhaul_dev`.
4. Set the `DATABASE_URL` environment variable, e.g.:

   ```bash
   export DATABASE_URL="postgresql+psycopg2://user:password@localhost:5432/backhaul_dev"
   ```

5. Run the development server:

   ```bash
   uvicorn app.main:app --reload
   ```

6. Open the interactive API docs at:

   - Swagger UI: `http://localhost:8000/docs`
   - ReDoc: `http://localhost:8000/redoc`

### Database tables (new projects or new tables)

To create all tables (including `planned_loads`, `haulier_routes`, `load_interests` for weekly/monthly routes):

```bash
python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
```

### If you already have a database (adding new columns)

If the `vehicles` table already exists and you add the `trailer_type` field to the code, add the column in PostgreSQL:

```bash
psql -d backhaul_dev -c "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS trailer_type VARCHAR(50);"
```

(Use your actual database name and connection if different.)

### Next steps to make it live

1. **Hosting and database**
   - Pick a host for the app (e.g. **Railway**, **Render**, **Fly.io**, or a VPS). Most can run a Python app and give you a URL.
   - Use a **managed PostgreSQL** (same provider, or **Supabase**, **Neon**, **AWS RDS**) and note the connection string.

2. **Environment variables (production)**
   - Set **`DATABASE_URL`** to your production Postgres URL, e.g.  
     `postgresql+psycopg2://user:password@host:5432/dbname`
   - Optional: **`DVLA_API_KEY`** if you use the DVLA lookup.
   - Optional: **`PLATFORM_FEE_PERCENT`** (default `8`) for your commission; see **docs/ARCHITECTURE.md** for the full payment flow.
   - Do **not** commit `.env` or real secrets to git; set variables in the host’s dashboard or CI.

3. **Run the app in production**
   - Install deps and run uvicorn **without** `--reload`, binding all interfaces:
     ```bash
     pip install -r requirements.txt
     uvicorn app.main:app --host 0.0.0.0 --port 8000
     ```
   - For more concurrency (e.g. under a reverse proxy), use multiple workers:
     ```bash
     uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
     ```
   - Create tables once (same as dev):
     ```bash
     python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
     ```

4. **Domain and HTTPS**
   - Point your domain to the host (A/CNAME or the host’s instructions). Railway/Render/Fly usually provide HTTPS and a default URL.
   - If you put a **reverse proxy** (e.g. Nginx, Caddy) in front, ensure it forwards to the app and keeps **Server-Sent Events** working (no buffering for `/api/alerts/stream`).

5. **Checks before go-live**
   - Call **`GET /health`** from the load balancer or monitoring; expect `{"status":"ok"}`.
   - Confirm the web UI and **Find backhaul** work; test **Live alerts** (SSE) in the browser.
   - If you add auth or a separate frontend later: set **CORS** in FastAPI and use env-based config (e.g. `ALLOWED_ORIGINS`).

6. **Later**
   - Backups: use your Postgres provider’s backups or scheduled pg_dump.
   - Auth: add login (e.g. JWT or session) so hauliers only see their vehicles and loaders only their loads.
   - DVLA/ULEZ: wire `DVLA_API_KEY` and ULEZ/CAZ checks when you’re ready.

### Next steps (product)

- Implement DVLA and ULEZ/CAZ integration services.
- Add login so hauliers and loaders only see their own data.

# Fixed User creation
