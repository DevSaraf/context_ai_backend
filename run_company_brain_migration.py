import sys
sys.path.insert(0, '.')
from sqlalchemy import text
from app.database import engine

def main():
    print("Running 001_company_brain.sql migration...")
    with open('migrations/001_company_brain.sql', 'r') as f:
        sql = f.read()

    # Split by double newlines or execute as one block
    # Postgres driver supports executing full scripts
    with engine.begin() as conn:
        conn.execute(text(sql))
        
    print("Migration complete!")

if __name__ == "__main__":
    main()
