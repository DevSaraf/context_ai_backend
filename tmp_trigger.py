import httpx
from app.database import SessionLocal
from sqlalchemy import text
from app.jwt_handler import create_access_token
import asyncio

db = SessionLocal()
user_row = db.execute(text("SELECT id, email, company_id FROM users WHERE email = 'savvytechno.dev@gmail.com';")).fetchone()
db.close()

token = create_access_token(data={"user_id": user_row.id, "sub": str(user_row.id)})

async def hit_synthesis():
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            "http://127.0.0.1:8001/brain/synthesize",
            json={"force": True, "min_cluster_size": 2},
            headers={"Authorization": f"Bearer {token}"}
        )
        data = res.json()
        print("Run Started:", data)
        if "id" in data:
            run_id = data["id"]
            print(f"Waiting for run {run_id} to finish...")
            for _ in range(30):
                await asyncio.sleep(2)
                res2 = await client.get(
                    f"http://127.0.0.1:8001/brain/runs/{run_id}",
                    headers={"Authorization": f"Bearer {token}"}
                )
                run_data = res2.json()
                status = run_data.get("status")
                print(f"Status: {status}")
                if status in ("completed", "failed"):
                    break

asyncio.run(hit_synthesis())
