from fastapi import FastAPI
from .database import engine
from . import models
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from .jwt_handler import create_access_token
from .database import SessionLocal
from . import models, schemas, auth
from .dependencies import get_current_user
import secrets

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()

models.Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {"message": "Context AI backend running"}


@app.post("/login")
def login(user: schemas.UserLogin, db: Session = Depends(get_db)):

    db_user = db.query(models.User).filter(models.User.email == user.email).first()

    if not db_user:
        return {"error": "User not found"}

    if not auth.verify_password(user.password, db_user.password):
        return {"error": "Invalid password"}

    token = create_access_token(
        data={"user_id": db_user.id}
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }

@app.get("/me")
def get_user_data(user_id: int = Depends(get_current_user)):
    return {
        "message": "Authenticated",
        "user_id": user_id
    }

# @app.post("/register")
# def register(user: schemas.UserCreate, db: Session = Depends(get_db)):

#     hashed_password = auth.hash_password(user.password)

#     new_user = models.User(
#     email=user.email,
#     password=hashed_password,
#     company_id=user.company_id
# )

#     db.add(new_user)
#     db.commit()
#     db.refresh(new_user)

#     return {"message": "User created"}

@app.post("/register")
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):

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

    return {
        "message": "User created",
        "api_key": api_key
    }