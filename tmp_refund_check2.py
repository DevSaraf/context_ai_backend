from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://postgres:password@127.0.0.1:5432/context_ai"
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    print("=== Query 1: refund rows with user_id, company_id, source_app, embedding null check ===")
    rows = conn.execute(text(
        "SELECT id, user_id, company_id, source_app, (embedding IS NULL) AS no_emb, substr(text,1,40) FROM knowledge_chunks WHERE text ILIKE '%refund%'"
    )).fetchall()
    if not rows:
        print("(0 rows)")
    for r in rows:
        print(f"id={r[0]} | user_id={r[1]} | company_id={r[2]} | source_app={r[3]} | no_emb={r[4]} | {r[5]}")

    print()
    print("=== Query 2: self-distance check for zero-vector detection ===")
    rows = conn.execute(text(
        "SELECT id, (embedding <=> embedding) AS self_dist FROM knowledge_chunks WHERE text ILIKE '%refund%'"
    )).fetchall()
    if not rows:
        print("(0 rows)")
    for r in rows:
        print(f"id={r[0]} | self_dist={r[1]}")

    print()
    print("=== Sanity: total row count in knowledge_chunks ===")
    total = conn.execute(text("SELECT COUNT(*) FROM knowledge_chunks")).scalar()
    print(f"Total rows: {total}")

    print()
    print("=== Sanity: all distinct company_id values ===")
    rows = conn.execute(text("SELECT DISTINCT company_id FROM knowledge_chunks")).fetchall()
    for r in rows:
        print(r[0])

    print()
    print("=== Sanity: all distinct user_id values ===")
    rows = conn.execute(text("SELECT DISTINCT user_id FROM knowledge_chunks")).fetchall()
    for r in rows:
        print(r[0])
