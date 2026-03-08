# Platoonix – test logins and sample data

Use this to test **manual entry** and **CSV bulk upload** (companies, vehicles, loads).

---

## 1. Login details

### Admin (built-in)

- **URL:** http://localhost:8000/login (or your deployed URL)
- **Email:** `admin@platoonix.local`
- **Password:** `change-me`

*(If you changed them in `.env` as `ADMIN_EMAIL` and `ADMIN_PASSWORD`, use those instead.)*

### Haulier and Loader

Create these from the **Admin** tab after logging in as admin:

1. Go to **Admin**.
2. **New haulier (company + login in one step):**
   - Company name: `Test Haulier Ltd`
   - Login email: `haulier@test.com`
   - Password: `test1234`
   - Click **Create company + login**.
3. **Loader (company + login):**
   - Company name: `Test Loader Co`
   - Login email: `loader@test.com`
   - Password: `test1234`
   - Click **Create loader account**.

Then you can log out and log in as:

- **Haulier:** `haulier@test.com` / `test1234`
- **Loader:** `loader@test.com` / `test1234`

---

## 2. Manual entry

### Company (Vehicles tab)

- **Company:** `Acme Transport`
- **Email:** `acme@test.com`
- **Phone:** `07123456789`
- Click **Add company**.

### Vehicle (Vehicles tab)

- **Company:** pick e.g. `Acme Transport` (or the one you just added)
- **Reg:** `AB12 CDE`
- **Base postcode:** `B1 1AA` (optional)
- **Artic:** Artic (or Rigid / Van)
- **Trailer:** Curtain (or Box, Flatbed, Other)
- Click **Add vehicle**.

### Load (Loads tab)

- **Shipper:** `Example Shipper`
- **Pickup:** `SW1A 1AA`
- **Delivery:** `B1 1AA`
- Click **Add load**.

---

## 3. CSV bulk upload

### Companies (hauliers)

1. Download the template: on **Vehicles** tab click **Hauliers CSV** (or go to `/download-templates/hauliers`).
2. Or create a file `hauliers.csv` with:

```csv
name,contact_email,contact_phone
Test Transport Ltd,contact@test.com,01234567890
North Freight Ltd,north@test.com,07890123456
```

3. On **Vehicles** tab use **Bulk upload companies** → choose the file → submit. You’ll see “Uploaded 2 hauliers” (or similar).

### Vehicles

Vehicles CSV uses **haulier_id** (the number from the companies table). After adding companies (manual or CSV), note their IDs in the table (or use 1, 2, … if you just created them in order).

1. Download the template: **Vehicles CSV** on the Vehicles tab.
2. Or create `vehicles.csv` (use `1` and `2` if your first two companies have IDs 1 and 2):

```csv
haulier_id,registration,vehicle_type,trailer_type,capacity_weight_kg,capacity_volume_m3
1,AB12CDE,artic,curtain_sider,26000,80
1,CD34FGH,rigid,box,18000,45
2,EF56IJK,artic,flatbed,26000,80
```

3. **Bulk upload vehicles** → choose the file → submit.

### Loads

1. On **Loads** tab click **CSV template** (or `/download-templates/loads`).
2. Or create `loads.csv`:

```csv
shipper_name,pickup_postcode,delivery_postcode,pickup_window_start,pickup_window_end,weight_kg,volume_m3,budget_gbp
Example Shipper,SW1A1AA,B11AA,2026-03-01 09:00,2026-03-01 11:00,10000,30,350
Acme Logistics,M1 1AA,B2 4AA,,,5000,20,
North Freight,NN4 5ET,LE1 1AA,,,8000,25,400
```

3. **Bulk upload loads** → choose the file → submit. Matching runs automatically; check the **Matches** tab.

---

## 4. Quick test order

1. **Login** as admin: `admin@platoonix.local` / `change-me`.
2. **Manual:** Add one company (e.g. Acme Transport), then one vehicle for that company, then one load (any shipper, pickup, delivery).
3. **CSV companies:** Upload `hauliers.csv` with 2–3 rows.
4. **CSV vehicles:** Upload `vehicles.csv` with `haulier_id` 1, 2, … matching your companies.
5. **CSV loads:** Upload `loads.csv` (Loads tab).
6. Open **Matches** to see matched loads/vehicles.
7. **Admin** tab: create haulier and loader logins, then log out and log in as haulier or loader to test their views.

---

## 5. CSV column reference

| Type     | Required columns              | Optional |
|----------|-------------------------------|----------|
| Hauliers | name, contact_email           | contact_phone |
| Vehicles | haulier_id, registration, vehicle_type | trailer_type, capacity_weight_kg, capacity_volume_m3 |
| Loads    | shipper_name, pickup_postcode, delivery_postcode | pickup_window_start, pickup_window_end, weight_kg, volume_m3, budget_gbp |

Dates/times: use formats like `2026-03-01 09:00` or `2026-03-01`.
