"""Auth routes: login, register, user info, profile management.
   Includes rate limiting, input sanitization, and account lockout."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
import secrets
import time
from collections import defaultdict

from app.database import get_db
from app import models, schemas, auth
from app.dependencies import get_current_user
from app.jwt_handler import create_access_token

router = APIRouter(tags=["auth"])


# ===== RATE LIMITING (in-memory, resets on server restart) =====
# Tracks failed attempts per IP and per email
_ip_attempts = defaultdict(list)       # {ip: [timestamp, timestamp, ...]}
_email_attempts = defaultdict(list)    # {email: [timestamp, timestamp, ...]}

MAX_ATTEMPTS_PER_IP = 10        # max login attempts per IP per window
MAX_ATTEMPTS_PER_EMAIL = 5      # max failed logins per email per window
RATE_WINDOW_SECONDS = 300       # 5-minute window
LOCKOUT_SECONDS = 600           # 10-minute lockout after exceeding limit


def _clean_old_attempts(attempts_list, window):
    """Remove attempts older than the window."""
    cutoff = time.time() - window
    return [t for t in attempts_list if t > cutoff]


def _check_rate_limit(key, tracker, max_attempts):
    """Returns True if rate limited, False if OK."""
    tracker[key] = _clean_old_attempts(tracker[key], RATE_WINDOW_SECONDS)
    if len(tracker[key]) >= max_attempts:
        # Check if still in lockout period
        latest = max(tracker[key]) if tracker[key] else 0
        if time.time() - latest < LOCKOUT_SECONDS:
            return True
    return False


def _record_attempt(key, tracker):
    """Record a failed attempt."""
    tracker[key].append(time.time())


def _get_client_ip(request: Request) -> str:
    """Get client IP, handling proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ===== LOGIN =====
@router.post("/login")
def login(user: schemas.UserLogin, request: Request, db: Session = Depends(get_db)):
    client_ip = _get_client_ip(request)

    # Rate limit by IP
    if _check_rate_limit(client_ip, _ip_attempts, MAX_ATTEMPTS_PER_IP):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")

    # Sanitize and validate email
    email = auth.sanitize(user.email).lower().strip()
    if not auth.is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    # Validate password length (prevent bcrypt DoS)
    if not user.password or len(user.password) > auth.MAX_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid password")

    if len(user.password) < auth.MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid password")

    # Rate limit by email
    if _check_rate_limit(email, _email_attempts, MAX_ATTEMPTS_PER_EMAIL):
        raise HTTPException(status_code=429, detail="Account temporarily locked. Try again in 10 minutes.")

    # Look up user
    db_user = db.query(models.User).filter(models.User.email == email).first()

    if not db_user:
        # Record failed attempt (use generic message to prevent email enumeration)
        _record_attempt(client_ip, _ip_attempts)
        _record_attempt(email, _email_attempts)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not auth.verify_password(user.password, db_user.password):
        _record_attempt(client_ip, _ip_attempts)
        _record_attempt(email, _email_attempts)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Success — clear failed attempts for this email
    _email_attempts.pop(email, None)

    token = create_access_token(data={"user_id": db_user.id})

    return {
        "access_token": token,
        "token_type": "bearer",
        "email": db_user.email,
        "company_id": db_user.company_id,
        "name": db_user.name
    }


# ===== REGISTER =====
@router.post("/register")
def register(user: schemas.UserCreate, request: Request, db: Session = Depends(get_db)):
    client_ip = _get_client_ip(request)

    # Rate limit by IP (prevent mass registration)
    if _check_rate_limit(client_ip, _ip_attempts, MAX_ATTEMPTS_PER_IP):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")

    # Sanitize and validate email
    email = auth.sanitize(user.email).lower().strip()
    if not auth.is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    # Validate password
    if not user.password or len(user.password) < auth.MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    if len(user.password) > auth.MAX_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Password too long")

    # Sanitize company_id
    company_id = auth.sanitize(user.company_id).strip() if user.company_id else "default"
    if len(company_id) > 100:
        raise HTTPException(status_code=400, detail="Company ID too long")

    # Check if user already exists
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        _record_attempt(client_ip, _ip_attempts)
        raise HTTPException(status_code=400, detail="Email already registered")

    try:
        hashed_password = auth.hash_password(user.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    api_key = secrets.token_hex(32)

    new_user = models.User(
        email=email,
        password=hashed_password,
        company_id=company_id,
        api_key=api_key
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"message": "User created", "api_key": api_key}


# ===== GET USER DATA =====
@router.get("/me")
def get_user_data(db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "company_id": user.company_id,
        "api_key": user.api_key,
        "created_at": str(user.created_at) if user.created_at else None,
    }


# ===== UPDATE PROFILE =====
@router.put("/profile")
def update_profile(
    data: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.email:
        clean_email = auth.sanitize(data.email).lower().strip()
        if not auth.is_valid_email(clean_email):
            raise HTTPException(status_code=400, detail="Invalid email format")
        if clean_email != user.email:
            conflict = db.query(models.User).filter(models.User.email == clean_email).first()
            if conflict:
                raise HTTPException(status_code=400, detail="Email already in use")
            user.email = clean_email

    if data.name is not None:
        user.name = auth.sanitize(data.name)[:100]  # Cap at 100 chars
    if data.company_id is not None:
        user.company_id = auth.sanitize(data.company_id)[:100]

    db.commit()
    return {"success": True, "message": "Profile updated"}


# ===== CHANGE PASSWORD =====
@router.post("/change-password")
def change_password(
    data: schemas.ChangePassword,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate lengths
    if not data.current_password or len(data.current_password) > auth.MAX_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid current password")

    if not data.new_password or len(data.new_password) < auth.MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    if len(data.new_password) > auth.MAX_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail="Password too long")

    if not auth.verify_password(data.current_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        user.password = auth.hash_password(data.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    return {"success": True, "message": "Password updated"}


# ===== DELETE ACCOUNT =====
@router.delete("/account")
def delete_account(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    from sqlalchemy import text
    db.execute(text("DELETE FROM feedback WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM search_logs WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM knowledge_chunks WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    db.commit()
    return {"success": True, "message": "Account deleted"}