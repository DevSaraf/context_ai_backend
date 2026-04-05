"""
KRAB Re-embed Script — Fixes all broken embeddings.

Run from project root:
    python fix_embeddings.py

What it does:
1. Finds all chunks where cosine self-distance is NaN (broken vectors)
2. Re-embeds them using the current working Gemini model
3. Updates the database

Safe to run multiple times — only touches broken chunks.
"""

import os
import sys
import math
import time

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.embedding import create_embedding
from sqlalchemy import text


def fix_embeddings():
    db = SessionLocal()
    
    print("=" * 60)
    print("KRAB — Re-embedding broken chunks")
    print("=" * 60)
    
    # Find all broken chunks (NaN self-distance = zero/invalid vector)
    print("\nFinding broken embeddings...")
    all_chunks = db.execute(text("""
        SELECT id, LEFT(text, 60) as preview, text,
               (embedding <=> embedding) as self_dist
        FROM knowledge_chunks
        WHERE embedding IS NOT NULL
        ORDER BY id
    """)).mappings().all()
    
    broken = []
    ok = []
    for c in all_chunks:
        sd = float(c["self_dist"]) if c["self_dist"] is not None else -1
        if math.isnan(sd) or sd < 0:
            broken.append(c)
        else:
            ok.append(c)
    
    # Also find chunks with NULL embeddings
    null_chunks = db.execute(text("""
        SELECT id, LEFT(text, 60) as preview, text
        FROM knowledge_chunks
        WHERE embedding IS NULL AND text IS NOT NULL AND text != ''
    """)).mappings().all()
    
    print(f"  OK embeddings: {len(ok)}")
    print(f"  Broken (NaN) embeddings: {len(broken)}")
    print(f"  NULL embeddings: {len(null_chunks)}")
    
    to_fix = list(broken) + list(null_chunks)
    
    if not to_fix:
        print("\nAll embeddings are healthy! Nothing to fix.")
        db.close()
        return
    
    print(f"\nRe-embedding {len(to_fix)} chunks...")
    print("(Gemini free tier: ~15 RPM, adding small delays)\n")
    
    fixed = 0
    failed = 0
    
    for i, chunk in enumerate(to_fix):
        chunk_id = chunk["id"]
        chunk_text = chunk["text"]
        preview = chunk["preview"]
        
        if not chunk_text or not chunk_text.strip():
            print(f"  [{i+1}/{len(to_fix)}] ID={chunk_id} — Skipping empty text")
            continue
        
        try:
            new_embedding = create_embedding(chunk_text)
            
            non_zero = sum(1 for v in new_embedding if abs(v) > 0.0001)
            if non_zero == 0:
                print(f"  [{i+1}/{len(to_fix)}] ID={chunk_id} — Got zero vector, skipping")
                failed += 1
                continue
            
            db.execute(text(
                "UPDATE knowledge_chunks SET embedding = CAST(:emb AS vector) WHERE id = :id"
            ), {"emb": str(new_embedding), "id": chunk_id})
            db.commit()
            
            fixed += 1
            print(f"  [{i+1}/{len(to_fix)}] ID={chunk_id} — Fixed ({non_zero}/768 dims) | {preview}")
            
            # Rate limit: bigger pause every 10 requests
            if (i + 1) % 10 == 0:
                print(f"  ... pausing 5s (rate limit) ...")
                time.sleep(5)
            else:
                time.sleep(0.5)
                
        except Exception as e:
            print(f"  [{i+1}/{len(to_fix)}] ID={chunk_id} — ERROR: {e}")
            failed += 1
            db.rollback()
            time.sleep(2)
    
    print(f"\n{'=' * 60}")
    print(f"DONE!")
    print(f"  Fixed: {fixed}")
    print(f"  Failed: {failed}")
    print(f"  Already OK: {len(ok)}")
    print(f"{'=' * 60}")
    
    # Verify
    print("\nVerifying...")
    still_broken = db.execute(text("""
        SELECT count(*) as cnt FROM knowledge_chunks
        WHERE embedding IS NOT NULL
        AND (embedding <=> embedding) = 'NaN'
    """)).scalar()
    
    if still_broken == 0:
        print("  All embeddings are now valid!")
    else:
        print(f"  {still_broken} chunks still have broken embeddings.")
    
    db.close()


if __name__ == "__main__":
    fix_embeddings()