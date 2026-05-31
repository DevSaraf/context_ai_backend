"""
Query the EXACT same database the live app on port 8000 uses.
DATABASE_URL taken directly from .env: postgresql://postgres:password@127.0.0.1:5432/context_ai
"""
from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://postgres:password@127.0.0.1:5432/context_ai"
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # Query 1
    r1 = conn.execute(text("SELECT COUNT(*) FROM knowledge_chunks WHERE company_id='KRAB'")).fetchone()
    print("=== Query 1: COUNT of KRAB chunks ===")
    print(r1[0])
    print()

    # Query 2
    print("=== Query 2: chunks containing 'refund' ===")
    rows = conn.execute(text("SELECT id, substr(text, 1, 60) FROM knowledge_chunks WHERE text ILIKE '%refund%'")).fetchall()
    if not rows:
        print("(0 rows)")
    for row in rows:
        print(f"id={row[0]}  |  {row[1]}")
