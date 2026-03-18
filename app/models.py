from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    company_id = Column(String)
    api_key = Column(String, unique=True)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(String, index=True)

    source_type = Column(String)  
    source_id = Column(Integer)

    text = Column(Text)

    embedding = Column(Vector(384))

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SearchLog(Base):
    """Track all searches for analytics"""
    __tablename__ = "search_logs"

    id = Column(Integer, primary_key=True, index=True)
    
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    company_id = Column(String, index=True)
    
    query = Column(Text)
    results_count = Column(Integer)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    """Track feedback on knowledge suggestions"""
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    company_id = Column(String, index=True)
    chunk_id = Column(Integer, ForeignKey("knowledge_chunks.id"), index=True)
    
    # Feedback type: 'helpful', 'not_helpful', 'used'
    feedback_type = Column(String, index=True)
    
    # Optional: the query that led to this suggestion
    query = Column(Text)
    
    # Similarity score at time of suggestion
    similarity_score = Column(Float)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())