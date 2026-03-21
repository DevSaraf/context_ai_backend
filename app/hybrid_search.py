"""
Hybrid Search Module
Combines pgvector semantic search with PostgreSQL full-text search (tsvector).
Uses Reciprocal Rank Fusion (RRF) to merge rankings from both methods.
"""

from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict
import math
from app.embedding import create_embedding


def hybrid_search(
    db: Session,
    filter_value,
    query: str,
    query_embedding: list,
    limit: int = 5,
    vector_weight: float = 0.6,
    text_weight: float = 0.4,
    rrf_k: int = 60,
    filter_by: str = "user_id",
) -> List[Dict]:
    """
    Hybrid search combining vector similarity + full-text search.

    Args:
        db:              Database session
        filter_value:    user_id or company_id value to filter by
        query:           User's search query (text)
        query_embedding: Pre-computed embedding vector
        limit:           Max results to return
        vector_weight:   Weight for vector search ranking (0-1)
        text_weight:     Weight for full-text search ranking (0-1)
        rrf_k:           RRF constant (higher = less weight to top ranks)
        filter_by:       "user_id" (default, per-user) or "company_id" (team mode)
    """
    # Validate filter_by to prevent SQL injection
    if filter_by not in ("user_id", "company_id"):
        filter_by = "user_id"

    try:
        results = db.execute(
            text(f"""
                WITH vector_results AS (
                    SELECT
                        id, text, source_type, source_id,
                        1 - (embedding <=> CAST(:embedding AS vector)) AS similarity,
                        resolution_score, created_at,
                        ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:embedding AS vector)) AS v_rank
                    FROM knowledge_chunks
                    WHERE {filter_by} = :filter_value
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
                    WHERE {filter_by} = :filter_value
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
                "filter_value": filter_value,
                "query": query,
                "vector_weight": vector_weight,
                "text_weight": text_weight,
                "rrf_k": rrf_k,
                "limit": limit,
            }
        ).mappings().all()

        enhanced = []
        for r in results:
            r_dict = dict(r)
            similarity = r_dict.get("similarity", 0) or 0
            if math.isinf(similarity) or math.isnan(similarity):
                similarity = 0.0
            resolution_score = r_dict.get("resolution_score") or 0.5

            hybrid_boost = 0.05 if r_dict.get("search_method") == "hybrid" else 0
            confidence = (similarity * 0.7) + (resolution_score * 0.3) + hybrid_boost
            r_dict["similarity"] = round(float(similarity), 4)
            r_dict["confidence"] = round(min(confidence + 0.1, 1.0), 3)
            r_dict["created_at"] = str(r_dict.get("created_at", ""))
            enhanced.append(r_dict)

        return enhanced

    except Exception as e:
        print(f"Hybrid search error (falling back to vector-only): {e}")
        return _vector_only_search(db, filter_value, query_embedding, limit, filter_by)


def _vector_only_search(
    db: Session,
    filter_value,
    query_embedding: list,
    limit: int = 5,
    filter_by: str = "user_id",
) -> List[Dict]:
    """Fallback: vector-only search."""
    if filter_by not in ("user_id", "company_id"):
        filter_by = "user_id"

    results = db.execute(
        text(f"""
            SELECT
                id, text, source_type, source_id,
                1 - (embedding <=> CAST(:embedding AS vector)) AS similarity,
                resolution_score, created_at
            FROM knowledge_chunks
            WHERE {filter_by} = :filter_value
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """),
        {"embedding": query_embedding, "filter_value": filter_value, "limit": limit}
    ).mappings().all()

    enhanced = []
    for r in results:
        r_dict = dict(r)
        similarity = r_dict.get("similarity", 0) or 0
        if math.isinf(similarity) or math.isnan(similarity):
            similarity = 0.0
        resolution_score = r_dict.get("resolution_score") or 0.5

        confidence = (similarity * 0.7) + (resolution_score * 0.3)
        r_dict["similarity"] = round(float(similarity), 4)
        r_dict["confidence"] = round(min(confidence + 0.1, 1.0), 3)
        r_dict["created_at"] = str(r_dict.get("created_at", ""))
        r_dict["search_method"] = "vector"
        enhanced.append(r_dict)

    return enhanced
