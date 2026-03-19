"""Auth routes: login, register, user info."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import secrets

from app.database import get_db
from app import models, schemas, auth
from app.dependencies import get_current_user
from app.jwt_handler import create_access_token

router = APIRouter(tags=["auth"])


@router.post("/login")
def login(user: schemas.UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()

    if not db_user:
        raise HTTPException(status_code=401, detail="User not found")

    if not auth.verify_password(user.password, db_user.password):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = create_access_token(data={"user_id": db_user.id})

    return {
        "access_token": token,
        "token_type": "bearer",
        "email": db_user.email,
        "company_id": db_user.company_id
    }


@router.get("/me")
def get_user_data(user_id: int = Depends(get_current_user)):
    return {"message": "Authenticated", "user_id": user_id}


@router.post("/register")
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    # Check if user already exists
    existing = db.query(models.User).filter(models.User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = auth.hash_password(user.password)
    api_key = secrets.token_hex(32)

    new_user = models.User(
        email=user.email,
        password=hashed_password,
        company_id=user.company_id,
        api_key=api_key
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"message": "User created", "api_key": api_key}
