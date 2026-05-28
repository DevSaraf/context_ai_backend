import httpx
import asyncio
from app.database import SessionLocal
from app.models import User
from app.jwt_handler import create_access_token
import io

async def test_upload():
    # Get user 3
    db = SessionLocal()
    user = db.query(User).filter_by(id=3).first()
    db.close()
    
    if not user:
        print("User 3 not found!")
        return

    # Create token
    token = create_access_token({"user_id": user.id, "email": user.email, "role": user.role})
    print(f"Token: {token[:20]}...")

    # Upload file
    url = "http://127.0.0.1:8001/knowledge/upload-file"
    headers = {"Authorization": f"Bearer {token}"}
    
    # We need to send a multipart/form-data request
    files = {
        "file": ("test_upload_file.txt", io.BytesIO(b"Hello world this is a test upload for debugging context ai backend."), "text/plain")
    }

    print(f"Uploading to {url}...")
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(url, headers=headers, files=files)
            print("Status:", res.status_code)
            print("Response:", res.text)
        except Exception as e:
            print("Exception:", e)

if __name__ == "__main__":
    asyncio.run(test_upload())
