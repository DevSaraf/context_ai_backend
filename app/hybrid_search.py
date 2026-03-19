"""
Hybrid Search Module
Combines pgvector semantic search with PostgreSQL full-text search (tsvector).
Uses Reciprocal Rank Fusion (RRF) to merge rankings from both methods.

Why hybrid?
- Vector search finds semantically similar content ("API timeout" matches "request latency")
- Full-text search catches exact keyword matches ("JWT" matches "JWT", vectors might miss this)
- Combined: best of both worlds, significantly better retrieval quality

Setup (run once):
    You need a tsvector column + GIN index on knowledge_chunks.
    Run the migration SQL below, or use the setup_hybrid_search() function.

Usage:
    from app.hybrid_search import hybrid_search

    results = hybrid_search(db, company_id, query, query_embedding, limit=5)
    # Returns list of dicts with: id, text, source_type, source_id, similarity,
    #   resolution_score, created_at, confidence, search_method
"""

from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict
from app.embedding import create_embedding


# ============== DATABASE MIGRATION ==============

MIGRATION_SQL = """
-- Add tsvector column if it doesn't exist
ALTER TABLE knowledge_chunks 
ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Populate search_vector from existing text
UPDATE knowledge_chunks 
SET search_vector = to_tsvector('english', COALESCE(text, ''))
WHERE search_vector IS NULL;

-- Create GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_search_vector 
ON knowledge_chunks USING GIN (search_vector);

-- Create trigger to auto-update search_vector on insert/update
CREATE OR REPLACE FUNCTION knowledge_chunks_search_trigger()
RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', COALESCE(NEW.text, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_knowledge_chunks_search ON knowledge_chunks;
CREATE TRIGGER trg_knowledge_chunks_search
BEFORE INSERT OR UPDATE OF text ON knowledge_chunks
FOR EACH ROW EXECUTE FUNCTION knowledge_chunks_search_trigger();
"""


def setup_hybrid_search(db: Session):
    """
    Run the migration to add full-text search support.
    Call this once, or add to your startup.
    
    Usage:
        from app.hybrid_search import setup_hybrid_search
        setup_hybrid_search(next(get_db()))
    """
    try:
        for statement in MIGRATION_SQL.strip().split(';'):
            statement = statement.strip()
            if statement:
                db.execute(text(statement))
        db.commit()
        print("Hybrid search setup complete!")
        return True
    except Exception as e:
        db.rollback()
        print(f"Hybrid search setup error: {e}")
        return False


# ============== HYBRID SEARCH ==============

def hybrid_search(
    db: Session,
    company_id: str,
    query: str,
    query_embedding: list,
    limit: int = 5,
    vector_weight: float = 0.6,
    text_weight: float = 0.4,
    rrf_k: int = 60,
) -> List[Dict]:
    """
    Hybrid search combining vector similarity + full-text search.
    
    Uses Reciprocal Rank Fusion (RRF) to merge results:
        RRF_score = weight_v / (k + rank_vector) + weight_t / (k + rank_text)
    
    This avoids the problem of comparing raw scores across different methods
    (cosine similarity 0-1 vs ts_rank which has a different scale).
    
    Args:
        db:              Database session
        company_id:      Company isolation
        query:           User's search query (text)
        query_embedding: Pre-computed embedding vector
        limit:           Max results to return
        vector_weight:   Weight for vector search ranking (0-1)
        text_weight:     Weight for full-text search ranking (0-1)
        rrf_k:           RRF constant (higher = less weight to top ranks)
    """
    try:
        results = db.execute(
            text("""
                WITH vector_results AS (
                    SELECT 
                        id, text, source_type, source_id,
                        1 - (embedding <=> CAST(:embedding AS vector)) AS similarity,
                        resolution_score, created_at,
                        ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:embedding AS vector)) AS v_rank
                    FROM knowledge_chunks
                    WHERE company_id = :company_id
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT 20
                ),
                text_results AS (
                    SELECT 
                        id,
                        ts_rank_cd(search_vector, plainto_tsquery('english', :query)) AS text_score,
                        ROW_NUMBER() OVER (
                            ORDER BY ts_rank_cd(search_vector, plainto_tsquery('english', :query)) DESC
                        ) AS t_rank
                    FROM knowledge_chunks
                    WHERE company_id = :company_id
                        AND search_vector @@ plainto_tsquery('english', :query)
                    LIMIT 20
                ),
                combined AS (
                    SELECT 
                        v.id, v.text, v.source_type, v.source_id,
                        v.similarity, v.resolution_score, v.created_at,
                        v.v_rank,
                        t.t_rank,
                        (
                            :vector_weight / (:rrf_k + v.v_rank) +
                            COALESCE(:text_weight / (:rrf_k + t.t_rank), 0)
                        ) AS rrf_score,
                        CASE 
                            WHEN t.t_rank IS NOT NULL THEN 'hybrid'
                            ELSE 'vector'
                        END AS search_method
                    FROM vector_results v
                    LEFT JOIN text_results t ON v.id = t.id
                )
                SELECT 
                    id, text, source_type, source_id,
                    similarity, resolution_score, created_at,
                    rrf_score, search_method
                FROM combined
                ORDER BY rrf_score DESC
                LIMIT :limit
            """),
            {
                "embedding": query_embedding,
                "company_id": company_id,
                "query": query,
                "vector_weight": vector_weight,
                "text_weight": text_weight,
                "rrf_k": rrf_k,
                "limit": limit,
            }
        ).mappings().all()

        # Enhance with confidence scores
        enhanced = []
        for r in results:
            r_dict = dict(r)
            similarity = r_dict.get("similarity", 0)
            resolution_score = r_dict.get("resolution_score") or 0.5

            # Boost confidence for hybrid matches (found by both methods)
            hybrid_boost = 0.05 if r_dict.get("search_method") == "hybrid" else 0
            confidence = (similarity * 0.7) + (resolution_score * 0.3) + hybrid_boost
            r_dict["confidence"] = round(min(confidence + 0.1, 1.0), 3)
            enhanced.append(r_dict)

        return enhanced

    except Exception as e:
        # If full-text search fails (column missing), fall back to vector-only
        print(f"Hybrid search error (falling back to vector-only): {e}")
        return _vector_only_search(db, company_id, query_embedding, limit)


def _vector_only_search(
    db: Session,
    company_id: str,
    query_embedding: list,
    limit: int = 5,
) -> List[Dict]:
    """Fallback: vector-only search (your original query)."""
    results = db.execute(
        text("""
            SELECT 
                id, text, source_type, source_id,
                1 - (embedding <=> CAST(:embedding AS vector)) AS similarity,
                resolution_score, created_at
            FROM knowledge_chunks
            WHERE company_id = :company_id
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """),
        {"embedding": query_embedding, "company_id": company_id, "limit": limit}
    ).mappings().all()

    enhanced = []
    for r in results:
        r_dict = dict(r)
        similarity = r_dict.get("similarity", 0)
        resolution_score = r_dict.get("resolution_score") or 0.5
        confidence = (similarity * 0.7) + (resolution_score * 0.3)
        r_dict["confidence"] = round(min(confidence + 0.1, 1.0), 3)
        r_dict["search_method"] = "vector"
        enhanced.append(r_dict)

    return enhanced
