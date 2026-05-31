from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://postgres:password@127.0.0.1:5432/context_ai"
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    print("=== Query 1: refund chunks, NO company_id or user_id filter ===")
    rows = conn.execute(text(
        "SELECT id, user_id, source_type, substr(text,1,60) FROM knowledge_chunks WHERE text ILIKE '%refund%'"
    )).fetchall()
    if not rows:
        print("(0 rows)")
    for r in rows:
        print(f"id={r[0]} | user_id={r[1]} | source_type={r[2]} | {r[3]}")

    print()
    print("=== Query 2: 14 days / 30 days chunks ===")
    rows = conn.execute(text(
        "SELECT id, user_id FROM knowledge_chunks WHERE text ILIKE '%14 days%' OR text ILIKE '%30 days%'"
    )).fetchall()
    if not rows:
        print("(0 rows)")
    for r in rows:
        print(f"id={r[0]} | user_id={r[1]}")
