import sys
sys.path.insert(0, '.')
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
res = db.execute(text("SELECT pg_typeof(embedding) FROM knowledge_chunks LIMIT 1;")).fetchone()
print("COLUMN TYPE:", res)
db.close()
