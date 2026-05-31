"""
Script to list tables and run queries on the live DB.
"""
from sqlalchemy import create_engine, text, inspect

DATABASE_URL = "postgresql://postgres:password@127.0.0.1:5432/context_ai"
engine = create_engine(DATABASE_URL)

print("=== \\dt (List Tables) ===")
insp = inspect(engine)
tables = insp.get_table_names()
for t in tables:
    print(t)
print()

with engine.connect() as conn:
    print("=== SELECT id, substr(text,1,50) FROM knowledge_chunks WHERE text ILIKE '%refund%'; ===")
    rows = conn.execute(text("SELECT id, substr(text,1,50) FROM knowledge_chunks WHERE text ILIKE '%refund%';")).fetchall()
    if not rows:
        print("(0 rows)")
    for r in rows:
        print(f"{r[0]} | {r[1]}")
    print()

    print("=== SELECT COUNT(*) FROM knowledge_chunks; ===")
    c = conn.execute(text("SELECT COUNT(*) FROM knowledge_chunks;")).scalar()
    print(c)
    print()

    print("=== SELECT DISTINCT source_id FROM knowledge_chunks; ===")
    rows = conn.execute(text("SELECT DISTINCT source_id FROM knowledge_chunks;")).fetchall()
    if not rows:
        print("(0 rows)")
    for r in rows:
        print(r[0])
