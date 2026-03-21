"""Search & RAG routes: search, context (extension), ask (dashboard), ticket match."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.dependencies import get_current_user
from app.embedding import create_embedding
from app.context_builder import build_context
from app.models import SearchLog
from app.hybrid_search import hybrid_search
from app.rag_engine import generate_answer, generate_ticket_response

router = APIRouter(tags=["search"])


def _get_user_info(db: Session, user_id: int):
    """Helper: get user, raise if not found."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _log_search(db: Session, user_id: int, company_id: str, query: str, count: int):
    """Helper: log a search for analytics."""
    log = SearchLog(
        user_id=user_id,
        company_id=company_id,
        query=query[:500],
        results_count=count
    )
    db.add(log)
    db.commit()


@router.post("/search")
def search(
    data: dict,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Semantic + hybrid search. Returns ranked chunks for this user."""
    user = _get_user_info(db, user_id)
    query = data.get("query") or data.get("prompt")

    if not query:
        return {"results": []}

    try:
        query_embedding = create_embedding(query)
        results = hybrid_search(db, user_id, query, query_embedding, limit=5, filter_by="user_id")
        return {"results": results}
    except Exception as e:
        print(f"Search error: {e}")
        return {"results": []}


@router.post("/context")
async def get_context(
    data: dict,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Extension context endpoint — returns raw chunks for the sidebar."""
    prompt = data.get("prompt")
    if not prompt:
        return {"context": "", "sources": []}

    user = _get_user_info(db, user_id)

    try:
        query_embedding = create_embedding(prompt)
        results = hybrid_search(db, user_id, prompt, query_embedding, limit=5, filter_by="user_id")

        _log_search(db, user_id, user.company_id, prompt, len(results))

        context = build_context(results)

        return {
            "context": context,
            "sources": results,
        }
    except Exception as e:
        print(f"Context error: {e}")
        return {"context": "", "sources": []}


@router.post("/ask")
async def ask_question(
    data: dict,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """RAG-powered Q&A: search → retrieve → generate answer with citations."""
    query = data.get("query") or data.get("prompt")
    if not query:
        raise HTTPException(status_code=400, detail="No query provided")

    user = _get_user_info(db, user_id)

    try:
        query_embedding = create_embedding(query)
        chunks = hybrid_search(db, user_id, query, query_embedding, limit=5, filter_by="user_id")

        rag_response = await generate_answer(query, chunks, mode="qa")

        _log_search(db, user_id, user.company_id, query, len(chunks))

        return {
            "answer": rag_response.answer,
            "citations": rag_response.citations,
            "confidence": rag_response.confidence,
            "has_answer": rag_response.has_answer,
            "chunks_used": rag_response.chunks_used,
            "sources": chunks,
        }
    except Exception as e:
        print(f"Ask error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to generate answer")


@router.post("/tickets/match")
async def match_ticket(
    data: dict,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Match a support ticket against past resolutions, generate suggested reply."""
    subject = data.get("subject", "")
    body = data.get("body", "")

    if not subject and not body:
        raise HTTPException(status_code=400, detail="Provide at least a subject or body")

    user = _get_user_info(db, user_id)

    try:
        search_text = f"{subject}. {body}".strip()
        query_embedding = create_embedding(search_text)
        similar_tickets = hybrid_search(db, user_id, search_text, query_embedding, limit=5, filter_by="user_id")

        rag_response = await generate_ticket_response(subject, body, similar_tickets)

        _log_search(db, user_id, user.company_id, f"[TICKET] {search_text}", len(similar_tickets))

        return {
            "suggested_response": rag_response.answer,
            "confidence": rag_response.confidence,
            "has_answer": rag_response.has_answer,
            "similar_tickets": similar_tickets,
            "citations": rag_response.citations,
        }
    except Exception as e:
        print(f"Ticket match error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to match ticket")
