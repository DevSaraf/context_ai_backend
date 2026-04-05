"""
KRAB — Hybrid Search (Fixed)
Searches BOTH user-uploaded chunks AND company-level connector-synced chunks.

FIX: Short chunks (e.g. "Dev is founder of krab") now get proper keyword boost
so they aren't drowned out by longer but less relevant chunks.
"""

from sqlalchemy.orm import Session
from sqlalchemy import text, or_, and_
from typing import List, Dict, Any, Optional
import math


def hybrid_search(
    db: Session,
    user_id: int,
    query: str,
    query_embedding: list,
    limit: int = 5,
    filter_by: str = "user_id",
    company_id: str = None,
) -> List[Dict[str, Any]]:
    """
    Hybrid search combining vector similarity + keyword matching.
    
    Searches TWO scopes:
    1. User's own uploads (user_id match, source_app='upload')
    2. Company connector-synced docs (company_id match, source_app != 'upload')
    
    FIX: Increased keyword boost from 0.15 to 0.35, added per-word incremental
    boost, and added a minimum similarity floor so short exact-match chunks
    score meaningfully even if their embedding similarity is low.
    """

    # Build the WHERE clause to cover both scopes
    if company_id:
        where_clause = """
            (
                (user_id = :user_id AND (source_app = 'upload' OR source_app IS NULL))
                OR
                (company_id = :company_id AND source_app IS NOT NULL AND source_app != 'upload')
            )
        """
        params = {
            "user_id": user_id,
            "company_id": company_id,
            "query_embedding": str(query_embedding),
            "limit": limit,
        }
    else:
        where_clause = "user_id = :user_id"
        params = {
            "user_id": user_id,
            "query_embedding": str(query_embedding),
            "limit": limit,
        }

    # Build keyword conditions for BM25-style boosting
    # FIX: Include shorter words (>=2 chars) and boost more aggressively
    words = [w.strip().lower() for w in query.split() if len(w.strip()) >= 2]
    keyword_boost = ""
    if words:
        # Each matching keyword adds 0.12 to the score (was 0.15 flat for any match)
        keyword_parts = []
        for i in range(len(words)):
            keyword_parts.append(
                f"CASE WHEN LOWER(text) ILIKE '%' || :kw{i} || '%' THEN 0.12 ELSE 0 END"
            )
        keyword_boost = "+ " + " + ".join(keyword_parts)

        for i, w in enumerate(words):
            params[f"kw{i}"] = w

    # FIX: Use GREATEST to set a minimum similarity floor of 0.01
    # This prevents NaN or negative cosine distances from zeroing out
    # chunks that have strong keyword matches
    sql = f"""
        SELECT 
            id,
            text,
            source_type,
            source_id,
            source_app,
            source_url,
            source_title,
            company_id,
            confidence,
            created_at,
            (
                GREATEST(1 - (embedding <=> CAST(:query_embedding AS vector)), 0.01)
                {keyword_boost}
            ) AS similarity
        FROM knowledge_chunks
        WHERE {where_clause}
            AND embedding IS NOT NULL
        ORDER BY similarity DESC
        LIMIT :limit
    """

    try:
        results = db.execute(text(sql), params).mappings().all()
        
        processed_results = []
        for r in results:
            sim_val = float(r["similarity"]) if r["similarity"] is not None else 0.0
            if math.isnan(sim_val):
                sim_val = 0.0
            else:
                sim_val = round(max(sim_val, 0.0), 4)

            processed_results.append({
                "id": r["id"],
                "text": r["text"],
                "source_type": r["source_type"],
                "source_id": r["source_id"],
                "source_app": r.get("source_app", "upload"),
                "source_url": r.get("source_url"),
                "source_title": r.get("source_title"),
                "confidence": sim_val,
                "similarity": sim_val,
            })
            
        return processed_results
        
    except Exception as e:
        print(f"Hybrid search error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return []