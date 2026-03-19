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
    
    # For Zendesk tickets: store CSAT-based quality score (0.0-1.0)
    resolution_score = Column(Float, nullable=True)


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


class ZendeskIntegration(Base):
    """Store Zendesk OAuth credentials per company"""
    __tablename__ = "zendesk_integrations"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String, unique=True, index=True)
    
    subdomain = Column(String)  # e.g., "mycompany" for mycompany.zendesk.com
    access_token = Column(String)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    
    # Sync tracking
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    tickets_imported = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZendeskTicket(Base):
    """Track imported Zendesk tickets"""
    __tablename__ = "zendesk_tickets"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String, index=True)
    
    zendesk_ticket_id = Column(Integer, index=True)  # Original Zendesk ID
    
    subject = Column(String)
    status = Column(String)  # open, pending, solved, closed
    priority = Column(String, nullable=True)
    
    # CSAT data
    csat_score = Column(Integer, nullable=True)  # 1-5 rating
    resolution_score = Column(Float, nullable=True)  # Normalized 0.0-1.0
    
    # Link to knowledge chunk created from this ticket
    chunk_id = Column(Integer, ForeignKey("knowledge_chunks.id"), nullable=True)
    
    ticket_created_at = Column(DateTime(timezone=True))
    ticket_updated_at = Column(DateTime(timezone=True))
    imported_at = Column(DateTime(timezone=True), server_default=func.now())