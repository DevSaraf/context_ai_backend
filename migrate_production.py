"""
KRAB Production Migration Script
=================================
Run this ONCE against your production database before (or after) first deploy.
Safe to re-run — every operation uses IF NOT EXISTS / IF EXISTS checks.

Usage:
    # Set your production DATABASE_URL first:
    export DATABASE_URL="postgresql://user:pass@your-azure-db:5432/context_ai"
    python migrate_production.py

    # Or on Windows:
    set DATABASE_URL=postgresql://user:pass@your-azure-db:5432/context_ai
    python migrate_production.py

What this script does:
    1. Enables pgvector extension
    2. Creates all core tables (users, knowledge_chunks, search_logs, feedback,
       zendesk_integrations, zendesk_tickets, widget_tickets)
    3. Adds missing columns (resolution_score, user_id, name, search_vector)
    4. Creates all indexes (vector, GIN, FK, etc.)
    5. Creates tsvector auto-update trigger
    6. Backfills search_vector for any existing rows
    7. Validates everything worked
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text, inspect

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("[FATAL] DATABASE_URL not set. Set it in .env or as an environment variable.")
    sys.exit(1)

print(f"[INFO] Connecting to: {DATABASE_URL.split('@')[-1]}")  # Print host only, not credentials
engine = create_engine(DATABASE_URL)


def run_sql(conn, sql, description, ignore_errors=False):
    """Run a SQL statement with logging."""
    try:
        conn.execute(text(sql))
        print(f"  ✅ {description}")
        return True
    except Exception as e:
        err_msg = str(e).lower()
        if "already exists" in err_msg or "duplicate" in err_msg:
            print(f"  ⏭️  {description} (already exists, skipped)")
            return True
        if ignore_errors:
            print(f"  ⚠️  {description} (skipped: {str(e)[:100]})")
            return True
        print(f"  ❌ {description} FAILED: {e}")
        return False


def migrate():
    print("\n" + "=" * 60)
    print("  KRAB Production Migration")
    print("=" * 60)

    with engine.connect() as conn:

        # ============================================================
        # STEP 1: Enable pgvector extension
        # ============================================================
        print("\n📦 Step 1: Enable pgvector extension")
        run_sql(conn, "CREATE EXTENSION IF NOT EXISTS vector;", "pgvector extension")

        # ============================================================
        # STEP 2: Create core tables
        # ============================================================
        print("\n📋 Step 2: Create core tables")

        # --- users ---
        run_sql(conn, """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                company_id VARCHAR(255),
                api_key VARCHAR(255) UNIQUE,
                name VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """, "users table")

        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_users_email ON users(email);",
                "users email index")

        # --- knowledge_chunks ---
        run_sql(conn, """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id SERIAL PRIMARY KEY,
                company_id VARCHAR(255),
                user_id INTEGER REFERENCES users(id),
                source_type VARCHAR(255),
                source_id INTEGER,
                text TEXT,
                embedding vector(768),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                resolution_score FLOAT
            );
        """, "knowledge_chunks table")

        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_company_id ON knowledge_chunks(company_id);",
                "knowledge_chunks company_id index")
        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_user_id ON knowledge_chunks(user_id);",
                "knowledge_chunks user_id index")

        # --- search_logs ---
        run_sql(conn, """
            CREATE TABLE IF NOT EXISTS search_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                company_id VARCHAR(255),
                query TEXT,
                results_count INTEGER,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """, "search_logs table")

        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_search_logs_user_id ON search_logs(user_id);",
                "search_logs user_id index")
        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_search_logs_company_id ON search_logs(company_id);",
                "search_logs company_id index")

        # --- feedback ---
        run_sql(conn, """
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                company_id VARCHAR(255),
                chunk_id INTEGER REFERENCES knowledge_chunks(id),
                feedback_type VARCHAR(50),
                query TEXT,
                similarity_score FLOAT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """, "feedback table")

        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_feedback_user_id ON feedback(user_id);",
                "feedback user_id index")
        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_feedback_company_id ON feedback(company_id);",
                "feedback company_id index")
        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_feedback_chunk_id ON feedback(chunk_id);",
                "feedback chunk_id index")
        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_feedback_type ON feedback(feedback_type);",
                "feedback type index")

        # --- zendesk_integrations ---
        run_sql(conn, """
            CREATE TABLE IF NOT EXISTS zendesk_integrations (
                id SERIAL PRIMARY KEY,
                company_id VARCHAR(255) UNIQUE,
                subdomain VARCHAR(255),
                access_token TEXT,
                refresh_token TEXT,
                token_expires_at TIMESTAMP WITH TIME ZONE,
                last_sync_at TIMESTAMP WITH TIME ZONE,
                tickets_imported INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """, "zendesk_integrations table")

        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_zendesk_integrations_company_id ON zendesk_integrations(company_id);",
                "zendesk_integrations company_id index")

        # --- zendesk_tickets ---
        run_sql(conn, """
            CREATE TABLE IF NOT EXISTS zendesk_tickets (
                id SERIAL PRIMARY KEY,
                company_id VARCHAR(255),
                zendesk_ticket_id INTEGER,
                subject VARCHAR(255),
                status VARCHAR(50),
                priority VARCHAR(50),
                csat_score INTEGER,
                resolution_score FLOAT,
                chunk_id INTEGER REFERENCES knowledge_chunks(id),
                ticket_created_at TIMESTAMP WITH TIME ZONE,
                ticket_updated_at TIMESTAMP WITH TIME ZONE,
                imported_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """, "zendesk_tickets table")

        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_zendesk_tickets_company_id ON zendesk_tickets(company_id);",
                "zendesk_tickets company_id index")
        run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_zendesk_tickets_zdid ON zendesk_tickets(zendesk_ticket_id);",
                "zendesk_tickets ticket_id index")

        # --- widget_tickets (no SQLAlchemy model — raw SQL table) ---
        run_sql(conn, """
            CREATE TABLE IF NOT EXISTS widget_tickets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                company_id VARCHAR(255),
                customer_name VARCHAR(200),
                customer_email VARCHAR(200),
                subject VARCHAR(500),
                message TEXT,
                ai_response TEXT,
                confidence FLOAT DEFAULT 0,
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """, "widget_tickets table")

        run_sql(conn, "CREATE INDEX IF NOT EXISTS idx_widget_tickets_user ON widget_tickets(user_id);",
                "widget_tickets user_id index")
        run_sql(conn, "CREATE INDEX IF NOT EXISTS idx_widget_tickets_status ON widget_tickets(status);",
                "widget_tickets status index")

        # ============================================================
        # STEP 3: Add missing columns to existing tables
        # ============================================================
        print("\n🔧 Step 3: Add missing columns")

        # Check what columns already exist
        inspector = inspect(engine)
        kc_columns = [c["name"] for c in inspector.get_columns("knowledge_chunks")] if inspector.has_table("knowledge_chunks") else []
        user_columns = [c["name"] for c in inspector.get_columns("users")] if inspector.has_table("users") else []

        if "resolution_score" not in kc_columns:
            run_sql(conn, "ALTER TABLE knowledge_chunks ADD COLUMN resolution_score FLOAT;",
                    "knowledge_chunks.resolution_score column")
        else:
            print("  ⏭️  knowledge_chunks.resolution_score already exists")

        if "user_id" not in kc_columns:
            run_sql(conn, "ALTER TABLE knowledge_chunks ADD COLUMN user_id INTEGER REFERENCES users(id);",
                    "knowledge_chunks.user_id column")
            run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_user_id ON knowledge_chunks(user_id);",
                    "knowledge_chunks user_id index")
        else:
            print("  ⏭️  knowledge_chunks.user_id already exists")

        if "name" not in user_columns:
            run_sql(conn, "ALTER TABLE users ADD COLUMN name VARCHAR(255);",
                    "users.name column")
        else:
            print("  ⏭️  users.name already exists")

        # ============================================================
        # STEP 4: Hybrid search — tsvector column + GIN index + trigger
        # ============================================================
        print("\n🔍 Step 4: Set up hybrid search (tsvector)")

        # Add search_vector column
        if "search_vector" not in kc_columns:
            run_sql(conn, """
                ALTER TABLE knowledge_chunks
                ADD COLUMN search_vector tsvector;
            """, "knowledge_chunks.search_vector column")
        else:
            print("  ⏭️  knowledge_chunks.search_vector already exists")

        # Create GIN index for fast full-text search
        run_sql(conn, """
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_search_vector
            ON knowledge_chunks USING GIN (search_vector);
        """, "GIN index on search_vector")

        # Create trigger function to auto-update search_vector on INSERT/UPDATE
        run_sql(conn, """
            CREATE OR REPLACE FUNCTION update_search_vector()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector := to_tsvector('english', COALESCE(NEW.text, ''));
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """, "search_vector trigger function")

        # Drop and recreate trigger (idempotent)
        run_sql(conn, """
            DROP TRIGGER IF EXISTS trg_update_search_vector ON knowledge_chunks;
        """, "drop old trigger (if exists)", ignore_errors=True)

        run_sql(conn, """
            CREATE TRIGGER trg_update_search_vector
            BEFORE INSERT OR UPDATE OF text ON knowledge_chunks
            FOR EACH ROW
            EXECUTE FUNCTION update_search_vector();
        """, "search_vector auto-update trigger")

        # ============================================================
        # STEP 5: Backfill search_vector for existing rows
        # ============================================================
        print("\n📝 Step 5: Backfill search_vector for existing rows")

        result = conn.execute(text(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE search_vector IS NULL AND text IS NOT NULL"
        ))
        null_count = result.scalar()

        if null_count and null_count > 0:
            run_sql(conn, """
                UPDATE knowledge_chunks
                SET search_vector = to_tsvector('english', COALESCE(text, ''))
                WHERE search_vector IS NULL AND text IS NOT NULL;
            """, f"backfilled {null_count} rows")
        else:
            print("  ⏭️  No rows need backfilling")

        # ============================================================
        # STEP 6: Create vector similarity index (HNSW for production performance)
        # ============================================================
        print("\n⚡ Step 6: Vector similarity index")

        # HNSW index for fast approximate nearest neighbor search
        # This makes vector search MUCH faster on large datasets (10k+ chunks)
        run_sql(conn, """
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw
            ON knowledge_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
        """, "HNSW vector index (cosine similarity)", ignore_errors=True)
        # ignore_errors=True because older pgvector versions may not support HNSW

        # ============================================================
        # COMMIT
        # ============================================================
        conn.commit()

        # ============================================================
        # STEP 7: Validate
        # ============================================================
        print("\n🔍 Step 7: Validation")

        expected_tables = [
            "users", "knowledge_chunks", "search_logs", "feedback",
            "zendesk_integrations", "zendesk_tickets", "widget_tickets"
        ]

        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        all_good = True
        for table in expected_tables:
            if table in existing_tables:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                print(f"  ✅ {table} — {count} rows")
            else:
                print(f"  ❌ {table} — MISSING!")
                all_good = False

        # Check search_vector column
        kc_cols = [c["name"] for c in inspector.get_columns("knowledge_chunks")]
        if "search_vector" in kc_cols:
            filled = conn.execute(text(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE search_vector IS NOT NULL"
            )).scalar()
            total = conn.execute(text("SELECT COUNT(*) FROM knowledge_chunks")).scalar()
            print(f"  ✅ search_vector — {filled}/{total} rows populated")
        else:
            print("  ❌ search_vector column — MISSING!")
            all_good = False

        # Check pgvector extension
        ext = conn.execute(text(
            "SELECT extname FROM pg_extension WHERE extname = 'vector'"
        )).fetchone()
        if ext:
            print("  ✅ pgvector extension — installed")
        else:
            print("  ❌ pgvector extension — NOT installed!")
            all_good = False

    # ============================================================
    # SUMMARY
    # ============================================================
    print("\n" + "=" * 60)
    if all_good:
        print("  ✅ ALL CHECKS PASSED — Database is ready for production!")
    else:
        print("  ⚠️  Some checks failed. Review the output above.")
    print("=" * 60 + "\n")

    return all_good


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
