# How live alerts work (when a match is found, how hauliers are alerted)

## How it works today

### 1. Haulier turns on “Live alerts”

- On the console they choose **Vehicle** and **Your postcode**, then click **Live alerts**.
- Their **browser** opens a long-lived connection to the server (`/api/alerts/stream`).
- The server keeps a list of “subscribers”: each one is “this vehicle + this postcode is listening”.

### 2. When a match is found

**New load (one-off)**  
- Someone creates a **new load** (via the Add data form or bulk upload).
- The server immediately checks: does this load match any **subscriber**? (Same rules as “Find backhaul”: within 25 miles of their postcode, fits vehicle trailer type and capacity.)
- For every matching subscriber, the server **pushes a message** down that subscriber’s open connection.

**Planned route match**  
- Someone adds a **planned load** or a **haulier route**, and the system finds a match (same day, within 25 miles, vehicle fits).
- The server (a) **pushes a message** to any subscriber who has that vehicle + postcode and is currently listening, and (b) creates a **“suggested”** match so it appears in **“Suggested for you (show interest)”** next time they open the page.

### 3. How the haulier is alerted (current behaviour)

- **Only while they have the console open and Live alerts on:**  
  The message arrives over the open connection; the **page updates** and the new match appears in the **alerts list** on the same page (e.g. “New load 5 · M1 1AA→B1 1AA” or “Planned match (Tue) · …”).
- So today, **“alerted” = they see the match appear on the webpage** in real time. No email, no SMS, no push to their phone when the tab is closed.

### 4. If they’re not on the page

- For **planned route matches**, they still get a “suggested” match: when they **next open** the console they see it under **“Suggested for you (show interest)”** and can click **Show interest**.
- For **new one-off loads**, if they weren’t listening at that moment, they only see the load if they later use **Find loads** or look at the Loads list. There is no email/SMS/push yet.

---

## Summary table

| Match type        | Haulier has Live alerts on (page open) | Haulier not on page / not listening   |
|-------------------|----------------------------------------|----------------------------------------|
| New load posted   | Message appears in alerts list on page | No alert; they can use “Find loads” later |
| Planned route     | Message appears in alerts list on page | “Suggested for you” when they next open the page |

---

## Possible future: alert when they’re not on the site

To alert hauliers when a match is found **and** they’re not on the console (e.g. by email or phone), you’d add something like:

1. **Email** – Store haulier email; when a match is found, call an email service (e.g. SendGrid, AWS SES) to send “A new load matches your vehicle at X – log in to view.”
2. **SMS** – Store mobile number; use an SMS provider (e.g. Twilio) to send a short “Match for vehicle X at postcode Y – check Platonix.”
3. **Push (mobile app / PWA)** – If you add an app or Progressive Web App, use push subscriptions so the browser or app can show a notification even when the tab is closed.

The **matching logic** (when to “alert”) is already in place; the missing piece for “alert when not on site” is a **delivery channel** (email/SMS/push) and the code to call it when we push an alert.
