import sys
sys.path.insert(0, '.')

from app.database import engine
from sqlalchemy import text

def add_column():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NULL"))
            conn.commit()
            print("Successfully added password_hash column to users table")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    add_column()
