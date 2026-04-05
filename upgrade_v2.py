"""
KRAB — Database Migration
Run this to create all new tables for the upgraded system.
"""

from sqlalchemy import create_engine, text
from app.models import Base
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/krab")


def run_migration():
    """Create all new tables. Safe to run multiple times (won't drop existing)."""
    engine = create_engine(DATABASE_URL)

    # Create all tables defined in models.py
    Base.metadata.create_all(bind=engine, checkfirst=True)

    print("Migration complete. Tables created/verified:")
    print("  - users (enhanced with role)")
    print("  - knowledge_chunks (enhanced with source_app, connector_id)")
    print("  - connector_configs")
    print("  - sync_logs")
    print("  - tickets")
    print("  - ticket_comments")
    print("  - ticket_events")
    print("  - ticket_counters")
    print("  - sla_policies")
    print("  - triggers")
    print("  - macros")
    print("  - help_articles")
    print("  - knowledge_health_reports")
    print("  - widget_tickets (enhanced with ticket_id FK)")

    # Add columns to existing tables if they don't exist
    with engine.connect() as conn:
        # Add new columns to knowledge_chunks if not present
        try:
            conn.execute(text("""
                ALTER TABLE knowledge_chunks
                ADD COLUMN IF NOT EXISTS source_app VARCHAR(50) DEFAULT 'upload',
                ADD COLUMN IF NOT EXISTS source_url TEXT,
                ADD COLUMN IF NOT EXISTS source_id VARCHAR(255),
                ADD COLUMN IF NOT EXISTS source_title VARCHAR(500),
                ADD COLUMN IF NOT EXISTS connector_id INTEGER REFERENCES connector_configs(id),
                ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP WITH TIME ZONE,
                ADD COLUMN IF NOT EXISTS is_stale BOOLEAN DEFAULT FALSE;
            """))
            conn.commit()
            print("  ✓ knowledge_chunks columns updated")
        except Exception as e:
            print(f"  ⚠ knowledge_chunks update: {e}")

        # Add new columns to users if not present
        try:
            conn.execute(text("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255),
                ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'agent',
                ADD COLUMN IF NOT EXISTS name VARCHAR(255),
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE;
            """))
            conn.commit()
            print("  ✓ users columns updated")
        except Exception as e:
            print(f"  ⚠ users update: {e}")

        # Add widget_tickets FK
        try:
            conn.execute(text("""
                ALTER TABLE widget_tickets
                ADD COLUMN IF NOT EXISTS ticket_id INTEGER REFERENCES tickets(id);
            """))
            conn.commit()
            print("  ✓ widget_tickets FK updated")
        except Exception as e:
            print(f"  ⚠ widget_tickets update: {e}")

        # Create indexes
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_chunks_company_source ON knowledge_chunks(company_id, source_app);
                CREATE INDEX IF NOT EXISTS ix_tickets_status_priority ON tickets(company_id, status, priority);
                CREATE INDEX IF NOT EXISTS ix_ticket_comments_ticket ON ticket_comments(ticket_id);
                CREATE INDEX IF NOT EXISTS ix_ticket_events_ticket ON ticket_events(ticket_id);
            """))
            conn.commit()
            print("  ✓ Indexes created")
        except Exception as e:
            print(f"  ⚠ Indexes: {e}")

    print("\nDone! Your database is ready for KRAB v2.")


if __name__ == "__main__":
    run_migration()
