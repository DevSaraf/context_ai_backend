"""
Run database migration for hybrid search.
Execute once: python run_migration.py

This adds:
- tsvector column (search_vector) to knowledge_chunks
- GIN index for fast full-text search
- Auto-update trigger for new/updated chunks
"""

import sys
sys.path.insert(0, '.')

from app.database import SessionLocal
from app.hybrid_search import setup_hybrid_search

def main():
    print("Running hybrid search migration...")
    db = SessionLocal()
    try:
        success = setup_hybrid_search(db)
        if success:
            print("\n[OK] Migration complete! Hybrid search is now enabled.")
            print("  - Added search_vector column")
            print("  - Created GIN index")
            print("  - Created auto-update trigger")
        else:
            print("\n[FAILED] Migration failed. Check the error above.")
            sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
