# Complete step-by-step setup (no coding experience needed)

Do **one step at a time**. Copy the text in the grey boxes, paste into Terminal, press **Enter**, and wait before doing the next step.

---

## STEP 1: Open Terminal on your Mac

1. Press **Command (⌘)** and **Space** together.
2. Type: **Terminal**
3. Press **Enter**.

A window opens with a line that might look like:  
`mac@MacBook ~ %`  
That’s Terminal. You’ll paste commands here.

---

## STEP 2: Go to your project folder

1. Click inside the Terminal window.
2. Copy the line below (triple-click it or select all, then Copy):

```
cd /Users/mac/Desktop/Platoonixcursor
```

3. In Terminal, press **Command (⌘)** and **V** to paste.
4. Press **Enter**.

You’re now in the project folder. The line at the end might show `Platoonixcursor`.

---

## STEP 3: Check if PostgreSQL is installed

Copy this, paste in Terminal, press **Enter**:

```
which psql
```

**What you might see:**

- A path like `/opt/homebrew/bin/psql` or `/usr/local/bin/psql`  
  → PostgreSQL is installed. **Skip to STEP 4.**

- Nothing, or `psql not found`  
  → You need to install PostgreSQL. Do **STEP 3a** and **3b** below.

---

### STEP 3a: Install Homebrew (only if you need to install PostgreSQL)

Copy, paste, press **Enter**:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow any instructions on screen (it may ask for your Mac password). Wait until it finishes.

Then run (copy the line that appears under “Next steps” for adding Homebrew to your PATH, or try this):

```
eval "$(/opt/homebrew/bin/brew shellenv)"
```

---

### STEP 3b: Install and start PostgreSQL

Copy, paste, press **Enter** (wait for it to finish):

```
brew install postgresql@14
```

Then run:

```
brew services start postgresql@14
```

Wait a few seconds. Then run again:

```
which psql
```

If you see a path, continue to **STEP 4**.

---

## STEP 4: Create the database

Run **each** of the following lines **one by one** (copy → paste in Terminal → Enter → wait, then next line).

**4.1 – Create the database:**

```
createdb backhaul_dev
```

If it says “already exists”, that’s OK. Continue.

**4.2 – Create the user:**

```
psql -d backhaul_dev -c "CREATE USER backhaul_user WITH PASSWORD 'backhaul_pass';"
```

If it says “already exists”, that’s OK.

**4.3 – Give permissions (first line):**

```
psql -d backhaul_dev -c "GRANT ALL PRIVILEGES ON DATABASE backhaul_dev TO backhaul_user;"
```

**4.4 – Give permissions (second line):**

```
psql -d backhaul_dev -c "ALTER DATABASE backhaul_dev OWNER TO backhaul_user;"
```

If you see errors, read them. “Already exists” usually means you can still go to STEP 5.

---

## STEP 5: Check the .env file in Cursor

1. Open **Cursor** (your code editor).
2. On the **left**, click the **folder icon** or **“Explorer”** so you see your project files.
3. Find the file named **`.env`** (it might be under the main folder **Platoonixcursor**).
4. **Click** on **`.env`** to open it.
5. It should contain **exactly** these two lines (nothing else, no extra symbols):

```
DATABASE_URL=postgresql+psycopg2://backhaul_user:backhaul_pass@localhost:5432/backhaul_dev
PLATFORM_FEE_PERCENT=0
```

6. If it doesn’t, **type** or **paste** those two lines in the file.
7. Press **Command (⌘)** and **S** to save.

Then go back to **Terminal** for STEP 6.

---

## STEP 6: Create the Python environment

In **Terminal** (same window where you did STEP 2), copy, paste, press **Enter**:

```
python3 -m venv .venv
```

Wait until the line with `%` or `$` comes back. No need to read the output.

---

## STEP 7: Turn on the Python environment

Copy, paste, press **Enter**:

```
source .venv/bin/activate
```

The start of the line should now show **(.venv)** in brackets, for example:

```
(.venv) mac@MacBook Platoonixcursor %
```

If you see **(.venv)**, continue. You need this for the next steps.

---

## STEP 8: Install the app’s dependencies

Copy, paste, press **Enter**:

```
pip install -r requirements.txt
```

Wait. You may see a lot of text and finally “Successfully installed …”. That’s good.

---

## STEP 9: Create the database tables

Copy this **entire** line (it’s one long line), paste in Terminal, press **Enter**:

```
python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
```

- If nothing appears and you get the prompt back → it worked.
- If you see an error about “connection” or “database” → check STEP 4 and STEP 5 (database created, `.env` correct).

---

## STEP 10: Start the app (the “website”)

Copy, paste, press **Enter**:

```
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

You should see something like:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

**Do not close this Terminal window.** Leave it open while you use the site.

---

## STEP 11: Open the website in your browser

1. Open **Safari**, **Chrome**, or **Firefox**.
2. Click in the **address bar** at the top.
3. Type exactly:

```
http://127.0.0.1:8000/
```

4. Press **Enter**.

You should see the **Backhaul Logistics Console** page (logo, “Bulk upload”, “Add data”, “Find backhaul”, etc.). That is your website.

---

## STEP 12 (optional): API docs

If you want to see the technical API page, in the browser address bar type:

```
http://127.0.0.1:8000/docs
```

You can ignore this if you only use the main console.

---

## Summary: every command in order

If you prefer to copy from one list, here are **all the Terminal commands in order** (run from the project folder, and from STEP 7 onward make sure you see **(.venv)** first):

```
cd /Users/mac/Desktop/Platoonixcursor
```

```
which psql
```

*(If needed:)*  
```
brew install postgresql@14
```  
```
brew services start postgresql@14
```

```
createdb backhaul_dev
```

```
psql -d backhaul_dev -c "CREATE USER backhaul_user WITH PASSWORD 'backhaul_pass';"
```

```
psql -d backhaul_dev -c "GRANT ALL PRIVILEGES ON DATABASE backhaul_dev TO backhaul_user;"
```

```
psql -d backhaul_dev -c "ALTER DATABASE backhaul_dev OWNER TO backhaul_user;"
```

*(Then do STEP 5 in Cursor – edit .env – then back to Terminal:)*

```
python3 -m venv .venv
```

```
source .venv/bin/activate
```

```
pip install -r requirements.txt
```

```
python -c "from app.database import Base, engine; from app import models; Base.metadata.create_all(bind=engine)"
```

```
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then in the browser open: **http://127.0.0.1:8000/**

---

## Next time you want to use the website

1. Open **Terminal**.
2. Run these three, one after the other:

```
cd /Users/mac/Desktop/Platoonixcursor
```

```
source .venv/bin/activate
```

```
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

3. In the browser open: **http://127.0.0.1:8000/**

To **stop** the website: in the Terminal window where it’s running, press **Control + C**.

---

## If something goes wrong

- **“Address already in use”**  
  Port 8000 is in use. Close other apps that might use it, or use a different port:  
  `uvicorn app.main:app --reload --host 0.0.0.0 --port 8001`  
  Then open: **http://127.0.0.1:8001/**

- **Database or connection error**  
  Check that STEP 4 and STEP 5 are done (database exists, `.env` has the correct `DATABASE_URL`).

- **“No module named app”**  
  Make sure you ran:  
  `cd /Users/mac/Desktop/Platoonixcursor`  
  and then:  
  `source .venv/bin/activate`  
  before the other commands.
