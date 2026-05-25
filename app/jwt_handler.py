import os
from jose import JWTError, jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# Every time a user logs in, this function generates a JSON Web Token (JWT) using the HS256 encryption algorithm.
# Why it's important: It cryptographically signs the user's ID and an expiration date (exp) using your server's secret key. When a user tries to search the database, your API verifies this token. If the token is tampered with or expired, the backend instantly rejects the request, making it impossible for hackers to access your endpoints.
def create_access_token(data: dict):
    to_encode = data.copy()

    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    return encoded_jwt