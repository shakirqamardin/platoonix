"""One-off: add trailer_type column to vehicles table if it doesn't exist."""
import os
import sys

# Run from project root so .env and app are found
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)
os.chdir(_project_root)

from sqlalchemy import text

from app.database import engine

if __name__ == "__main__":
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS trailer_type VARCHAR(50)"))
        print("Done. trailer_type column is ready.")
    except Exception as e:
        print("Error:", e)
        sys.exit(1)
