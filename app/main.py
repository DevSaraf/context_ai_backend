from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text, func as sql_func
from datetime import datetime, timedelta
import secrets

from app.database import Base, engine, get_db
from app.models import KnowledgeChunk, SearchLog, Feedback
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


def calculate_confidence(similarity: float, helpful: int, not_helpful: int, used: int, age_days: float) -> float:
    """
    Calculate confidence score based on:
    - Vector similarity (40% weight)
    - Historical feedback (40% weight)
    - Recency (20% weight)
    """
    # Similarity component (0-1, scaled to 0-0.4)
    sim_score = max(0, min(1, similarity)) * 0.4
    
    # Feedback component (0-0.4)
    total_feedback = helpful + not_helpful
    if total_feedback > 0:
        helpful_rate = helpful / total_feedback
        # Boost for more feedback (more reliable)
        feedback_confidence = min(1, total_feedback / 10)  # Max at 10 feedback items
        feedback_score = helpful_rate * feedback_confidence * 0.4
    else:
        # No feedback yet - neutral score
        feedback_score = 0.2
    
    # Add small boost for usage
    usage_boost = min(0.05, used * 0.01)
    
    # Recency component (0-0.2)
    # Knowledge older than 365 days gets lower score
    if age_days <= 30:
        recency_score = 0.2
    elif age_days <= 90:
        recency_score = 0.15
    elif age_days <= 365:
        recency_score = 0.1
    else:
        recency_score = 0.05
    
    confidence = sim_score + feedback_score + usage_boost + recency_score
    return round(min(1.0, confidence), 3)


@app.get("/")
def root():
    return {"message": "Context AI backend running"}


# TEMPORARY: Test endpoint without auth (for development only)
@app.post("/test/upload")
def test_upload_knowledge(data: dict, db: Session = Depends(get_db)):
    """Upload knowledge without auth - FOR TESTING ONLY"""

    email = data.get("email")
    content = data.get("content")

    if not email:
        return {"error": "Email required"}
    if not content:
        return {"error": "Content required"}

    # Find user by email
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        return {"error": f"User with email {email} not found"}

    company_id = user.company_id
    chunks = chunk_text(content)

    for chunk in chunks:
        embedding = create_embedding(chunk)
        item = KnowledgeChunk(
            company_id=company_id,
            source_type=data.get("source_type", "document"),
            source_id=data.get("source_id", 1),
            text=chunk,
            embedding=embedding
        )
        db.add(item)

    db.commit()

    return {"message": "Knowledge uploaded", "chunks": len(chunks), "company_id": company_id}


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
        "token_type": "bearer",
        "email": db_user.email,
        "company_id": db_user.company_id
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
def upload_knowledge(data: dict, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):

    # Get user's company_id
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"error": "User not found"}

    company_id = user.company_id
    content = data.get("content")

    if not content:
        return {"error": "No content provided"}

    chunks = chunk_text(content)

    for chunk in chunks:

        embedding = create_embedding(chunk)

        item = KnowledgeChunk(
            company_id=company_id,
            source_type=data.get("source_type", "document"),
            source_id=data.get("source_id", 1),
            text=chunk,
            embedding=embedding
        )

        db.add(item)

    db.commit()

    return {"message": "Knowledge stored with chunking", "chunks": len(chunks)}

@app.post("/search")
def search(data: dict, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"results": []}

    company_id = user.company_id
    query = data.get("query") or data.get("prompt")

    if not query:
        return {"results": []}

    try:
        query_embedding = create_embedding(query)

        # Simple query that works with existing schema
        results = db.execute(
            text("""
                SELECT 
                    id,
                    text, 
                    source_type, 
                    source_id,
                    1 - (embedding <=> CAST(:embedding AS vector)) AS similarity,
                    created_at
                FROM knowledge_chunks
                WHERE company_id = :company_id
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT 5
            """),
            {"embedding": query_embedding, "company_id": company_id}
        ).mappings().all()

        # Add confidence based on similarity
        enhanced_results = []
        for r in results:
            r_dict = dict(r)
            similarity = r_dict.get('similarity', 0)
            r_dict['confidence'] = round(similarity * 0.8 + 0.2, 3)
            enhanced_results.append(r_dict)

        return {"results": enhanced_results}

    except Exception as e:
        print("Search error:", e)
        import traceback
        traceback.print_exc()
        return {"results": []}

@app.post("/context")
def get_context(data: dict, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):

    prompt = data.get("prompt")
    if not prompt:
        return {"context": "", "sources": []}

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"context": "", "sources": []}

    company_id = user.company_id

    try:
        query_embedding = create_embedding(prompt)

        # Simple query that works with existing schema
        results = db.execute(
            text("""
                SELECT 
                    id,
                    text, 
                    source_type, 
                    source_id,
                    1 - (embedding <=> CAST(:embedding AS vector)) AS similarity,
                    created_at
                FROM knowledge_chunks
                WHERE company_id = :company_id
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT 5
            """),
            {"embedding": query_embedding, "company_id": company_id}
        ).mappings().all()

        # Add confidence based on similarity
        enhanced_results = []
        for r in results:
            r_dict = dict(r)
            similarity = r_dict.get('similarity', 0)
            # Simple confidence = similarity (will improve when feedback columns exist)
            r_dict['confidence'] = round(similarity * 0.8 + 0.2, 3)  # Scale to 0.2-1.0
            enhanced_results.append(r_dict)

        # Sort by confidence and take top 5
        enhanced_results.sort(key=lambda x: x['confidence'], reverse=True)
        enhanced_results = enhanced_results[:5]

        # Log the search
        search_log = SearchLog(
            user_id=user_id,
            company_id=company_id,
            query=prompt[:500],
            results_count=len(enhanced_results)
        )
        db.add(search_log)
        db.commit()

        context = build_context(enhanced_results)

        return {
            "context": context,
            "sources": enhanced_results
        }

    except Exception as e:
        print("Context error:", e)
        return {"context": "", "sources": []}


# ============== FEEDBACK ENDPOINTS ==============

@app.post("/feedback")
def submit_feedback(feedback: schemas.FeedbackCreate, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):
    """Submit feedback for a knowledge chunk"""
    
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"error": "User not found"}

    # Verify chunk exists using raw SQL to avoid model column issues
    chunk_exists = db.execute(
        text("SELECT id FROM knowledge_chunks WHERE id = :chunk_id"),
        {"chunk_id": feedback.chunk_id}
    ).fetchone()
    
    if not chunk_exists:
        return {"error": "Knowledge chunk not found"}

    # Create feedback record
    fb = Feedback(
        user_id=user_id,
        company_id=user.company_id,
        chunk_id=feedback.chunk_id,
        feedback_type=feedback.feedback_type,
        query=feedback.query,
        similarity_score=feedback.similarity_score
    )
    db.add(fb)
    db.commit()

    return {"success": True, "message": f"Feedback '{feedback.feedback_type}' recorded"}


@app.get("/feedback/chunk/{chunk_id}")
def get_chunk_feedback(chunk_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):
    """Get feedback stats for a specific chunk from the Feedback table"""
    
    # Count feedback from the Feedback table
    helpful = db.query(Feedback).filter(
        Feedback.chunk_id == chunk_id,
        Feedback.feedback_type == 'helpful'
    ).count()
    
    not_helpful = db.query(Feedback).filter(
        Feedback.chunk_id == chunk_id,
        Feedback.feedback_type == 'not_helpful'
    ).count()
    
    used = db.query(Feedback).filter(
        Feedback.chunk_id == chunk_id,
        Feedback.feedback_type == 'used'
    ).count()

    return {
        "chunk_id": chunk_id,
        "helpful_count": helpful,
        "not_helpful_count": not_helpful,
        "used_count": used,
        "helpful_rate": helpful / max(1, helpful + not_helpful)
    }


# ============== ANALYTICS ENDPOINTS ==============

@app.get("/analytics")
def get_analytics(db: Session = Depends(get_db), user_id: int = Depends(get_current_user)):
    """Get analytics for the user's company"""
    
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"error": "User not found"}

    company_id = user.company_id

    # Total searches
    total_searches = db.query(SearchLog).filter(SearchLog.company_id == company_id).count()

    # Searches in last 7 days
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_searches = db.query(SearchLog).filter(
        SearchLog.company_id == company_id,
        SearchLog.created_at >= week_ago
    ).count()

    # Total feedback
    total_feedback = db.query(Feedback).filter(Feedback.company_id == company_id).count()

    # Helpful rate
    helpful_count = db.query(Feedback).filter(
        Feedback.company_id == company_id,
        Feedback.feedback_type == 'helpful'
    ).count()
    
    not_helpful_count = db.query(Feedback).filter(
        Feedback.company_id == company_id,
        Feedback.feedback_type == 'not_helpful'
    ).count()

    total_ratings = helpful_count + not_helpful_count
    helpful_rate = helpful_count / max(1, total_ratings)

    # Usage rate
    used_count = db.query(Feedback).filter(
        Feedback.company_id == company_id,
        Feedback.feedback_type == 'used'
    ).count()
    
    usage_rate = used_count / max(1, total_searches)

    # Top sources by helpful feedback
    top_sources = db.execute(
        text("""
            SELECT source_type, COUNT(*) as count,
                   SUM(helpful_count) as helpful,
                   SUM(not_helpful_count) as not_helpful
            FROM knowledge_chunks
            WHERE company_id = :company_id
            GROUP BY source_type
            ORDER BY helpful DESC
            LIMIT 5
        """),
        {"company_id": company_id}
    ).mappings().all()

    # Searches by day (last 7 days)
    searches_by_day = db.execute(
        text("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM search_logs
            WHERE company_id = :company_id
            AND created_at >= :week_ago
            GROUP BY DATE(created_at)
            ORDER BY date
        """),
        {"company_id": company_id, "week_ago": week_ago}
    ).mappings().all()

    # Total knowledge chunks
    total_chunks = db.query(KnowledgeChunk).filter(KnowledgeChunk.company_id == company_id).count()

    return {
        "total_searches": total_searches,
        "recent_searches": recent_searches,
        "total_feedback": total_feedback,
        "helpful_rate": round(helpful_rate, 3),
        "usage_rate": round(usage_rate, 3),
        "total_chunks": total_chunks,
        "top_sources": [dict(s) for s in top_sources],
        "searches_by_day": [{"date": str(s["date"]), "count": s["count"]} for s in searches_by_day]
    }
