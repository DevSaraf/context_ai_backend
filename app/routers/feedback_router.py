"""Feedback & Analytics routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta

from app.database import get_db
from app import models, schemas
from app.dependencies import get_current_user
from app.models import KnowledgeChunk, SearchLog, Feedback

router = APIRouter(tags=["feedback & analytics"])


# ============== FEEDBACK ==============

@router.post("/feedback")
def submit_feedback(
    feedback: schemas.FeedbackCreate,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Submit feedback for a knowledge chunk."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    chunk_exists = db.execute(
        text("SELECT id FROM knowledge_chunks WHERE id = :chunk_id"),
        {"chunk_id": feedback.chunk_id}
    ).fetchone()

    if not chunk_exists:
        raise HTTPException(status_code=404, detail="Knowledge chunk not found")

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


@router.get("/feedback/chunk/{chunk_id}")
def get_chunk_feedback(
    chunk_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Get feedback stats for a specific chunk."""
    helpful = db.query(Feedback).filter(
        Feedback.chunk_id == chunk_id, Feedback.feedback_type == "helpful"
    ).count()

    not_helpful = db.query(Feedback).filter(
        Feedback.chunk_id == chunk_id, Feedback.feedback_type == "not_helpful"
    ).count()

    used = db.query(Feedback).filter(
        Feedback.chunk_id == chunk_id, Feedback.feedback_type == "used"
    ).count()

    return {
        "chunk_id": chunk_id,
        "helpful_count": helpful,
        "not_helpful_count": not_helpful,
        "used_count": used,
        "helpful_rate": helpful / max(1, helpful + not_helpful)
    }


# ============== ANALYTICS ==============

@router.get("/analytics")
def get_analytics(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Get analytics for the user's company."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    company_id = user.company_id
    week_ago = datetime.utcnow() - timedelta(days=7)

    total_searches = db.query(SearchLog).filter(SearchLog.company_id == company_id).count()

    recent_searches = db.query(SearchLog).filter(
        SearchLog.company_id == company_id,
        SearchLog.created_at >= week_ago
    ).count()

    total_feedback = db.query(Feedback).filter(Feedback.company_id == company_id).count()

    helpful_count = db.query(Feedback).filter(
        Feedback.company_id == company_id, Feedback.feedback_type == "helpful"
    ).count()

    not_helpful_count = db.query(Feedback).filter(
        Feedback.company_id == company_id, Feedback.feedback_type == "not_helpful"
    ).count()

    total_ratings = helpful_count + not_helpful_count
    helpful_rate = helpful_count / max(1, total_ratings)

    used_count = db.query(Feedback).filter(
        Feedback.company_id == company_id, Feedback.feedback_type == "used"
    ).count()
    usage_rate = used_count / max(1, total_searches)

    top_sources = db.execute(
        text("""
            SELECT source_type, COUNT(*) as count
            FROM knowledge_chunks WHERE company_id = :company_id
            GROUP BY source_type ORDER BY count DESC LIMIT 5
        """),
        {"company_id": company_id}
    ).mappings().all()

    searches_by_day = db.execute(
        text("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM search_logs WHERE company_id = :company_id AND created_at >= :week_ago
            GROUP BY DATE(created_at) ORDER BY date
        """),
        {"company_id": company_id, "week_ago": week_ago}
    ).mappings().all()

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
