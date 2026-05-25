import sys
sys.path.insert(0, '.')

from app.database import SessionLocal
from app.models import User

def check_user():
    db = SessionLocal()
    try:
        user = db.query(User).first()
        print(f"Successfully queried user! password_hash field: {getattr(user, 'password_hash', 'Not found but no error')}")
    except Exception as e:
        print(f"Error querying: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_user()
