import sys
sys.path.insert(0, '.')
from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
res = db.execute(text("SELECT id, user_id, company_id, source_app, text FROM knowledge_chunks;")).fetchall()
for row in res:
    print(dict(row._mapping))
db.close()
