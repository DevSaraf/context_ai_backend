"""
KRAB Debug Script — Run from project root:
    python debug_search.py

Fixed for pgvector — no float8[] casts.
"""

import os
import sys
import math

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.embedding import create_embedding
from sqlalchemy import text


def debug():
    db = SessionLocal()
    
    query = "who is founder of krab"
    
    print("=" * 60)
    print(f"DEBUG: Searching for '{query}'")
    print("=" * 60)
    
    # Step 1: All chunks
    print("\n--- STEP 1: All chunks in DB ---")
    all_chunks = db.execute(text(
        "SELECT id, LEFT(text, 80) as preview, source_type, "
        "embedding IS NOT NULL as has_embedding, "
        "vector_dims(embedding) as embed_dims "
        "FROM knowledge_chunks ORDER BY id"
    )).mappings().all()
    
    if not all_chunks:
        print("  NO CHUNKS IN DB AT ALL!")
        db.close()
        return
    
    for c in all_chunks:
        print(f"  ID={c['id']} | has_embed={c['has_embedding']} | dims={c['embed_dims']} | {c['preview']}")
    
    # Step 2: Find the "founder" chunk
    print("\n--- STEP 2: Check 'founder' chunk ---")
    founder_chunks = db.execute(text(
        "SELECT id, text, embedding IS NOT NULL as has_embedding, "
        "vector_dims(embedding) as embed_dims "
        "FROM knowledge_chunks WHERE LOWER(text) LIKE '%founder%'"
    )).mappings().all()
    
    if not founder_chunks:
        print("  NO CHUNK CONTAINS 'founder'! It doesn't exist in DB.")
        print("  Upload 'Dev is founder of krab' again.")
        db.close()
        return
    
    for fc in founder_chunks:
        print(f"  Found: ID={fc['id']} | has_embed={fc['has_embedding']} | dims={fc['embed_dims']}")
        print(f"  Text: '{fc['text']}'")
        
        if not fc['has_embedding']:
            print("  This chunk has NO EMBEDDING!")
    
    # Step 3: Check for zero vectors via self-distance
    print("\n--- STEP 3: Zero-vector check ---")
    for fc in founder_chunks:
        result = db.execute(text(
            "SELECT (embedding <=> embedding) as self_dist FROM knowledge_chunks WHERE id = :id"
        ), {"id": fc["id"]}).mappings().first()
        
        if result:
            self_dist = float(result["self_dist"]) if result["self_dist"] is not None else -1
            if math.isnan(self_dist) or self_dist < 0:
                print(f"  Chunk ID={fc['id']}: self_distance={self_dist} -> ZERO/INVALID VECTOR")
            elif abs(self_dist) < 0.0001:
                print(f"  Chunk ID={fc['id']}: self_distance={self_dist} -> Valid embedding")
            else:
                print(f"  Chunk ID={fc['id']}: self_distance={self_dist} -> Unusual")
    
    # Step 4: Query embedding
    print("\n--- STEP 4: Create query embedding ---")
    query_embedding = create_embedding(query)
    non_zero_q = sum(1 for v in query_embedding if abs(v) > 0.0001)
    print(f"  Query embedding: {len(query_embedding)} dims, {non_zero_q} non-zero values")
    
    if non_zero_q == 0:
        print("  QUERY EMBEDDING IS ZERO! Gemini API is failing.")
        db.close()
        return
    
    # Step 5: Similarity scores for ALL chunks
    print("\n--- STEP 5: Similarity scores (cosine) ---")
    results = db.execute(text("""
        SELECT 
            id,
            LEFT(text, 60) as preview,
            (1 - (embedding <=> CAST(:qe AS vector))) as cosine_sim
        FROM knowledge_chunks
        WHERE embedding IS NOT NULL
        ORDER BY cosine_sim DESC
    """), {"qe": str(query_embedding)}).mappings().all()
    
    for r in results:
        sim = float(r["cosine_sim"]) if r["cosine_sim"] is not None else 0
        if math.isnan(sim):
            sim_str = "NaN (BROKEN)"
        elif sim < 0.01:
            sim_str = f"{sim:.6f} (VERY LOW)"
        else:
            sim_str = f"{sim:.4f}"
        print(f"  ID={r['id']} | sim={sim_str} | {r['preview']}")
    
    # Step 6: Dimension consistency
    print("\n--- STEP 6: Dimension check ---")
    dims = db.execute(text(
        "SELECT id, vector_dims(embedding) as dims "
        "FROM knowledge_chunks WHERE embedding IS NOT NULL"
    )).mappings().all()
    
    dim_set = set()
    for d in dims:
        dim_set.add(d["dims"])
    
    print(f"  Chunk dimensions found: {dim_set}")
    print(f"  Query embedding dims: {len(query_embedding)}")
    
    if len(dim_set) > 1:
        print("  DIMENSION MISMATCH between chunks!")
        for d in dims:
            print(f"    Chunk ID={d['id']}: {d['dims']} dims")
    
    if dim_set and len(query_embedding) not in dim_set:
        print(f"  QUERY DIM ({len(query_embedding)}) != CHUNK DIM ({dim_set})!")
        print("  Embedding model changed. Need to re-embed all chunks.")
    elif dim_set:
        print("  Dimensions consistent")
    
    # Step 7: Keyword match
    print("\n--- STEP 7: Keyword match test ---")
    kw_results = db.execute(text("""
        SELECT id, LEFT(text, 80) as preview,
            CASE WHEN LOWER(text) LIKE '%%founder%%' THEN 'YES' ELSE 'no' END as has_founder,
            CASE WHEN LOWER(text) LIKE '%%krab%%' THEN 'YES' ELSE 'no' END as has_krab
        FROM knowledge_chunks
    """)).mappings().all()
    
    for r in kw_results:
        if r['has_founder'] == 'YES' or r['has_krab'] == 'YES':
            print(f"  ID={r['id']} | founder={r['has_founder']} | krab={r['has_krab']} | {r['preview']}")
    
    print("\n" + "=" * 60)
    print("DONE — paste this output to Claude for the fix")
    print("=" * 60)
    
    db.close()


if __name__ == "__main__":
    debug()