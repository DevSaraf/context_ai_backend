"""Search & RAG routes: search, context (extension), ask (dashboard), ticket match.
   FIXED: passes company_id to hybrid_search so connector-synced docs are searchable.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app import models
from app.dependencies import get_current_user
from app.embedding import create_embedding
from app.context_builder import build_context
from app.models import SearchLog, User
from app.hybrid_search import hybrid_search
from app.rag_engine import generate_answer, generate_ticket_response

router = APIRouter(tags=["search"])


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
    user: User = Depends(get_current_user)
):
    """Semantic + hybrid search. Returns ranked chunks for this user + company connectors."""
    query = data.get("query") or data.get("prompt")

    if not query:
        return {"results": []}

    try:
        query_embedding = create_embedding(query)
        results = hybrid_search(
            db, user.id, query, query_embedding,
            limit=5,
            company_id=user.company_id,  # FIXED: include connector docs
        )
        return {"results": results}
    except Exception as e:
        print(f"Search error: {e}")
        return {"results": []}


@router.post("/raw-search")
def raw_search(
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Literal text search inside stored chunks (no embeddings).
    Searches both user uploads and company connector docs.
    """
    query = data.get("query")
    if not query:
        return {"results": []}

    try:
        words = [w.strip() for w in query.split() if len(w.strip()) > 2]
        if not words:
            return {"results": []}

        conditions = " OR ".join([f"text ILIKE :w{i}" for i in range(len(words))])

        params = {"user_id": user.id, "company_id": user.company_id}
        for i, w in enumerate(words):
            params[f"w{i}"] = f"%{w}%"

        sql = f"""
            SELECT id, source_type, source_app, source_url, source_title, text, created_at
            FROM knowledge_chunks
            WHERE (
                (user_id = :user_id AND (source_app = 'upload' OR source_app IS NULL))
                OR
                (company_id = :company_id AND source_app IS NOT NULL AND source_app != 'upload')
            )
            AND ({conditions})
            ORDER BY created_at DESC
            LIMIT 20
        """

        results = db.execute(text(sql), params).mappings().all()
        return {"results": [dict(r) for r in results]}
    except Exception as e:
        print(f"Raw search error: {e}")
        return {"results": []}


@router.post("/context")
async def get_context(
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Extension context endpoint — returns raw chunks + AI answer for the sidebar."""
    prompt = data.get("prompt")
    if not prompt:
        return {"context": "", "sources": [], "answer": "", "confidence": 0, "has_answer": False}

    try:
        query_embedding = create_embedding(prompt)
        results = hybrid_search(
            db, user.id, prompt, query_embedding,
            limit=5,
            company_id=user.company_id,
        )

        _log_search(db, user.id, user.company_id, prompt, len(results))

        context = build_context(results)

        answer = ""
        confidence = 0
        has_answer = False
        try:
            if results:
                rag_response = await generate_answer(prompt, results, mode="qa")
                answer = rag_response.answer or ""
                confidence = rag_response.confidence or 0
                has_answer = rag_response.has_answer
        except Exception as ai_err:
            print(f"AI answer generation error (non-fatal): {ai_err}")

        return {
            "context": context,
            "sources": results,
            "answer": answer,
            "confidence": confidence,
            "has_answer": has_answer,
        }
    except Exception as e:
        print(f"Context error: {e}")
        return {"context": "", "sources": [], "answer": "", "confidence": 0, "has_answer": False}


@router.post("/ask")
async def ask_question(
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """RAG-powered Q&A: search → retrieve → generate answer with citations."""
    query = data.get("query") or data.get("prompt")
    if not query:
        raise HTTPException(status_code=400, detail="No query provided")

    try:
        query_embedding = create_embedding(query)
        chunks = hybrid_search(
            db, user.id, query, query_embedding,
            limit=5,
            company_id=user.company_id,
        )

        rag_response = await generate_answer(query, chunks, mode="qa")

        _log_search(db, user.id, user.company_id, query, len(chunks))

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
    user: User = Depends(get_current_user)
):
    """Match a support ticket against past resolutions, generate suggested reply."""
    subject = data.get("subject", "")
    body = data.get("body", "")
    tone = data.get("tone", "professional")

    if not subject and not body:
        raise HTTPException(status_code=400, detail="Provide at least a subject or body")

    try:
        search_text = f"{subject}. {body}".strip()
        query_embedding = create_embedding(search_text)
        similar_tickets = hybrid_search(
            db, user.id, search_text, query_embedding,
            limit=5,
            company_id=user.company_id,
        )

        rag_response = await generate_ticket_response(subject, body, similar_tickets, tone=tone)

        _log_search(db, user.id, user.company_id, f"[TICKET] {search_text}", len(similar_tickets))

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