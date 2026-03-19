"""Add resolution_score column to knowledge_chunks table"""
from app.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text('ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS resolution_score FLOAT'))
    conn.commit()
    print('Column added successfully!')
