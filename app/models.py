"""
KRAB — Enhanced Models
Adds: Connectors, Help Center, Knowledge Health
"""

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime, JSON,
    ForeignKey, Enum as SQLEnum, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from .database import Base
import enum


# ============================================================
# ENUMS
# ============================================================

class ConnectorStatus(str, enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    SYNCING = "syncing"
    ERROR = "error"




class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String(255))  # legacy field
    password_hash = Column(String(255), nullable=True)  # new field for future
    name = Column(String(255))
    company_id = Column(String(100), nullable=False, index=True)
    api_key = Column(String(255), unique=True, index=True)
    role = Column(String(50), default="agent")  # admin, agent, viewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    connectors = relationship("ConnectorConfig", back_populates="created_by_user")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)

    # User-level ownership: each user sees only their own uploads
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)

    text = Column(Text, nullable=False)
    embedding = Column(Vector(768))

    source_type = Column(String(50), default="document")
    source_app = Column(String(50), default="upload")  # upload, google_drive, notion, slack, confluence, github
    source_url = Column(Text)  # link back to original document
    source_id = Column(String(255))  # external ID from the source app (changed from Integer)
    source_title = Column(String(500))  # document/page title
    connector_id = Column(Integer, ForeignKey("connector_configs.id"), nullable=True)
    metadata_ = Column("metadata", JSON)
    confidence = Column(Float, default=0.0)
    last_synced_at = Column(DateTime(timezone=True))
    is_stale = Column(Boolean, default=False)  # flagged by knowledge health check
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # For Zendesk tickets: store CSAT-based quality score (0.0-1.0)
    resolution_score = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_chunks_company_source", "company_id", "source_app"),
    )


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

    subdomain = Column(String)
    access_token = Column(String)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)

    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    tickets_imported = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ZendeskTicket(Base):
    """Track imported Zendesk tickets"""
    __tablename__ = "zendesk_tickets"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String, index=True)

    zendesk_ticket_id = Column(Integer, index=True)

    subject = Column(String)
    status = Column(String)
    priority = Column(String, nullable=True)

    csat_score = Column(Integer, nullable=True)
    resolution_score = Column(Float, nullable=True)

    chunk_id = Column(Integer, ForeignKey("knowledge_chunks.id"), nullable=True)

    ticket_created_at = Column(DateTime(timezone=True))
    ticket_updated_at = Column(DateTime(timezone=True))
    imported_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# CONNECTOR SYSTEM
# ============================================================

class ConnectorConfig(Base):
    __tablename__ = "connector_configs"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)
    connector_type = Column(String(50), nullable=False)  # google_drive, notion, slack, confluence, github, jira, hubspot
    display_name = Column(String(255))
    status = Column(String(30), default=ConnectorStatus.DISCONNECTED)

    # OAuth tokens (encrypted in production)
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expires_at = Column(DateTime(timezone=True))

    # Connector-specific config
    config = Column(JSON, default=dict)  # e.g., {"subdomain": "...", "workspace_id": "...", "repo_list": [...]}

    # Sync settings
    sync_frequency_minutes = Column(Integer, default=60)
    last_sync_at = Column(DateTime(timezone=True))
    last_sync_status = Column(String(30))
    last_sync_message = Column(Text)
    documents_indexed = Column(Integer, default=0)

    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    created_by_user = relationship("User", back_populates="connectors")

    __table_args__ = (
        UniqueConstraint("company_id", "connector_type", name="uq_company_connector"),
    )


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    connector_id = Column(Integer, ForeignKey("connector_configs.id"), nullable=False, index=True)
    company_id = Column(String(100), nullable=False)
    status = Column(String(30))  # started, completed, failed
    documents_added = Column(Integer, default=0)
    documents_updated = Column(Integer, default=0)
    documents_deleted = Column(Integer, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))


# ============================================================
# HELP CENTER / PUBLIC KB
# ============================================================

class HelpArticle(Base):
    __tablename__ = "help_articles"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    slug = Column(String(500), index=True)
    body = Column(Text, nullable=False)
    body_html = Column(Text)
    category = Column(String(100), index=True)
    section = Column(String(100))
    tags = Column(JSON, default=list)

    status = Column(String(20), default="draft")  # draft, published, archived
    is_promoted = Column(Boolean, default=False)

    # Tracking
    view_count = Column(Integer, default=0)
    helpful_count = Column(Integer, default=0)
    not_helpful_count = Column(Integer, default=0)

    author_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    published_at = Column(DateTime(timezone=True))


# ============================================================
# KNOWLEDGE HEALTH
# ============================================================

class KnowledgeHealthReport(Base):
    __tablename__ = "knowledge_health_reports"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)

    total_documents = Column(Integer, default=0)
    stale_documents = Column(Integer, default=0)  # >6 months without update
    broken_links = Column(Integer, default=0)
    contradiction_count = Column(Integer, default=0)
    coverage_gaps = Column(JSON, default=list)  # top unanswered queries
    unused_documents = Column(Integer, default=0)  # never cited in answers
    freshness_score = Column(Float)  # 0-100
    overall_score = Column(Float)  # 0-100

    details = Column(JSON)  # full breakdown
    created_at = Column(DateTime(timezone=True), server_default=func.now())
