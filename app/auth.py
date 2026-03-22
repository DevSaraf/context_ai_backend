from passlib.context import CryptContext
import re
import html

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ===== Password security =====
MAX_PASSWORD_LENGTH = 72  # bcrypt truncates at 72 bytes anyway
MIN_PASSWORD_LENGTH = 6

def hash_password(password: str):
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError("Password too long")
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    if len(plain_password) > MAX_PASSWORD_LENGTH:
        return False
    return pwd_context.verify(plain_password, hashed_password)

# ===== Input sanitization =====
def sanitize(text: str) -> str:
    """Strip HTML tags and escape special characters"""
    clean = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
    clean = html.escape(clean)             # Escape &, <, >, ", '
    return clean.strip()

# ===== Email validation =====
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

def is_valid_email(email: str) -> bool:
    if not email or len(email) > 120:
        return False
    if '<' in email or '>' in email or ';' in email:
        return False
    return bool(EMAIL_REGEX.match(email))