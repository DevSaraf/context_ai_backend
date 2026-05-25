import os
from app.database import engine, DATABASE_URL
from sqlalchemy import inspect

def check_db():
    print(f"DATABASE_URL used by app: {DATABASE_URL}")
    print(f"Environment DATABASE_URL: {os.environ.get('DATABASE_URL')}")
    
    inspector = inspect(engine)
    try:
        columns = inspector.get_columns('users')
        print(f"Columns in 'users' table:")
        for c in columns:
            print(f" - {c['name']} ({c['type']})")
    except Exception as e:
        print(f"Error inspecting: {e}")

if __name__ == "__main__":
    check_db()
