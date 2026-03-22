"""Run tracking migration on Render database."""
import os
from sqlalchemy import create_engine, text

# Get DATABASE_URL from environment (set this to your Render DB URL)
db_url = os.getenv("DATABASE_URL")

if not db_url:
    print("ERROR: DATABASE_URL not set!")
    print("Get it from Render Dashboard → platoonix → Environment → DATABASE_URL")
    exit(1)

# Read migration SQL
with open("app/migrations/add_tracking.sql", "r") as f:
    sql = f.read()

# Connect and execute
engine = create_engine(db_url)
with engine.connect() as conn:
    conn.execute(text(sql))
    conn.commit()
    print("✅ Migration successful!")
