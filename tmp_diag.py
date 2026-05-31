"""Diagnose why Google Drive chunks don't show in Knowledge Base."""
from app.database import SessionLocal
from app.models import ConnectorConfig, KnowledgeChunk, SyncLog

db = SessionLocal()

# 1. Google Drive connector config
cfg = db.query(ConnectorConfig).filter_by(connector_type="google_drive").first()
if not cfg:
    print("NO google_drive ConnectorConfig found!")
else:
    print("=== ConnectorConfig ===")
    print(f"  id:                {cfg.id}")
    print(f"  created_by:        {cfg.created_by}")
    print(f"  company_id:        {cfg.company_id}")
    print(f"  status:            {cfg.status}")
    print(f"  documents_indexed: {cfg.documents_indexed}")
    print(f"  config keys:       {list((cfg.config or {}).keys())}")
    print(f"  selected_file_ids: {(cfg.config or {}).get('selected_file_ids')}")

# 2. Google Drive chunks
print("\n=== Chunks with source_app='google_drive' ===")
gd_chunks = db.query(KnowledgeChunk).filter(
    KnowledgeChunk.source_app == "google_drive"
).all()
print(f"  Count: {len(gd_chunks)}")
for c in gd_chunks:
    print(f"  id={c.id}  user_id={c.user_id}  connector_id={c.connector_id}  "
          f"title={c.source_title!r}  text_len={len(c.text)}")

# 3. Chunks linked to this connector
if cfg:
    print(f"\n=== Chunks with connector_id={cfg.id} ===")
    linked = db.query(KnowledgeChunk).filter(
        KnowledgeChunk.connector_id == cfg.id
    ).all()
    print(f"  Count: {len(linked)}")
    for c in linked:
        print(f"  id={c.id}  user_id={c.user_id}  source_app={c.source_app}  "
              f"title={c.source_title!r}")

# 4. Latest sync logs for google_drive
print("\n=== Recent SyncLogs ===")
if cfg:
    logs = db.query(SyncLog).filter_by(connector_id=cfg.id).order_by(
        SyncLog.id.desc()
    ).limit(5).all()
    for sl in logs:
        print(f"  id={sl.id}  status={sl.status}  added={sl.documents_added}  "
              f"error={sl.error_message!r}  completed={sl.completed_at}")

# 5. What user_id=3 sees (the exact query from /knowledge/documents)
print("\n=== What user_id=3 sees via /knowledge/documents ===")
from sqlalchemy import text
rows = db.execute(text("""
    SELECT id, source_type, source_app, source_title, created_at
    FROM knowledge_chunks
    WHERE user_id = 3
    ORDER BY created_at DESC
    LIMIT 30
""")).mappings().all()
print(f"  Count: {len(rows)}")
for r in rows:
    print(f"  id={r['id']}  source_type={r['source_type']}  "
          f"source_app={r['source_app']}  title={r['source_title']}")

# 6. Check for chunks with NULL user_id
print("\n=== Chunks with user_id IS NULL ===")
null_chunks = db.query(KnowledgeChunk).filter(
    KnowledgeChunk.user_id.is_(None)
).all()
print(f"  Count: {len(null_chunks)}")
for c in null_chunks:
    print(f"  id={c.id}  source_app={c.source_app}  connector_id={c.connector_id}  "
          f"title={c.source_title!r}")

db.close()
