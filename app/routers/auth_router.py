"""Auth routes: login, register, user info, profile management."""

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


@router.put("/profile")
def update_profile(
    data: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.email and data.email != user.email:
        conflict = db.query(models.User).filter(models.User.email == data.email).first()
        if conflict:
            raise HTTPException(status_code=400, detail="Email already in use")
        user.email = data.email

    if data.name is not None:
        user.name = data.name
    if data.company_id is not None:
        user.company_id = data.company_id

    db.commit()
    return {"success": True, "message": "Profile updated"}


@router.post("/change-password")
def change_password(
    data: schemas.ChangePassword,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not auth.verify_password(data.current_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    user.password = auth.hash_password(data.new_password)
    db.commit()
    return {"success": True, "message": "Password updated"}


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
