from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

print('--- Procedures ---')
res1 = db.execute(text('SELECT id, title, company_id, status FROM procedures ORDER BY id DESC LIMIT 20;')).fetchall()
for row in res1:
    print(row)

print('\n--- User savvytechno.dev@gmail.com ---')
res2 = db.execute(text("SELECT id, company_id, role, email FROM users WHERE email = 'savvytechno.dev@gmail.com';")).fetchall()
for row in res2:
    print(row)

db.close()
