import os
from sqlalchemy import text
from app.database import engine

def main():
    sql_file = "002_connectors.sql"
    with open(sql_file, 'r') as f:
        sql = f.read()

    print(f"Executing {sql_file}...")
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    print("Execution complete.")

if __name__ == '__main__':
    main()
