from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
import secrets

from app.database import Base, engine, get_db
from app.models import KnowledgeChunk
from app import models, schemas, auth
from app.dependencies import get_current_user
from app.jwt_handler import create_access_token
from app.embedding import create_embedding
from app.chunking import chunk_text
from app.context_builder import build_context

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (chrome extensions, localhost, etc.)
    allow_credentials=False,  # Must be False when using allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)


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

@app.post("/knowledge/upload")
def upload_knowledge(data: dict, db: Session = Depends(get_db)):

    chunks = chunk_text(data["content"])

    for chunk in chunks:

        embedding = create_embedding(chunk)

        item = KnowledgeChunk(
            company_id=data["company_id"],
            source_type="document",
            source_id=1,
            text=chunk,
            embedding=embedding
        )

        db.add(item)

    db.commit()

    return {"message": "Knowledge stored with chunking"}

@app.post("/search")
def search(data: dict, db: Session = Depends(get_db)):

    query = data.get("query") or data.get("prompt")

    if not query:
        return {"results": []}

    try:
        query_embedding = create_embedding(query)

        results = db.execute(
            text("""
                SELECT text, source_type, source_id,
                1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM knowledge_chunks
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT 5
            """),
            {"embedding": query_embedding}
        ).mappings().all()

        return {"results": [dict(r) for r in results]}

    except Exception as e:
        print("Search error:", e)
        return {"results": []}

@app.post("/context")
def get_context(data: dict, db: Session = Depends(get_db)):

    prompt = data.get("prompt")
    if not prompt:
        return {"context": "", "sources": []}

    try:
        query_embedding = create_embedding(prompt)

        results = db.execute(
            text("""
                SELECT text, source_type, source_id,
                1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM knowledge_chunks
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT 5
            """),
            {"embedding": query_embedding}
        ).mappings().all()

        context = build_context(results)

        return {
            "context": context,
            "sources": [dict(r) for r in results]
        }

    except Exception as e:
        print("Context error:", e)
        return {"context": "", "sources": []}
