"""
KRAB — Enhanced Models
Adds: Connectors, Ticket Lifecycle, SLA Policies, Triggers, Macros, Knowledge Health
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


class TicketStatus(str, enum.Enum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    SOLVED = "solved"
    CLOSED = "closed"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketChannel(str, enum.Enum):
    EMAIL = "email"
    WIDGET = "widget"
    API = "api"
    MANUAL = "manual"
    SLACK = "slack"

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
    assigned_tickets = relationship("Ticket", back_populates="assigned_agent", foreign_keys="Ticket.assigned_agent_id")
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
# TICKET SYSTEM (Zendesk-style)
# ============================================================

class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)
    ticket_number = Column(String(20), nullable=False, index=True)  # e.g., KRAB-001
    subject = Column(String(500), nullable=False)
    description = Column(Text)  # initial message body

    status = Column(String(20), default=TicketStatus.NEW, index=True)
    priority = Column(String(20), default=TicketPriority.NORMAL, index=True)
    channel = Column(String(20), default=TicketChannel.MANUAL)

    # Requester info
    requester_email = Column(String(255), index=True)
    requester_name = Column(String(255))

    # Assignment
    assigned_agent_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    assigned_group = Column(String(100))

    # Tags & categorization
    tags = Column(JSON, default=list)
    category = Column(String(100))

    # AI analysis
    ai_intent = Column(String(100))  # refund, bug_report, how_to, complaint, feature_request
    ai_sentiment = Column(String(20))  # positive, neutral, negative, frustrated
    ai_language = Column(String(10), default="en")
    ai_confidence = Column(Float)
    ai_suggested_response = Column(Text)

    # SLA tracking
    sla_policy_id = Column(Integer, ForeignKey("sla_policies.id"), nullable=True)
    first_response_at = Column(DateTime(timezone=True))
    first_response_due_at = Column(DateTime(timezone=True))
    resolution_due_at = Column(DateTime(timezone=True))
    sla_breach = Column(Boolean, default=False)

    # Satisfaction
    csat_rating = Column(Integer)  # 1-5
    csat_comment = Column(Text)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    solved_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))

    # Relationships
    assigned_agent = relationship("User", back_populates="assigned_tickets")
    comments = relationship("TicketComment", back_populates="ticket", order_by="TicketComment.created_at")
    events = relationship("TicketEvent", back_populates="ticket", order_by="TicketEvent.created_at")

    __table_args__ = (
        UniqueConstraint("company_id", "ticket_number", name="uq_company_ticket_number"),
        Index("ix_tickets_status_priority", "company_id", "status", "priority"),
    )


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    author_type = Column(String(20), default="agent")  # agent, customer, system, ai
    author_name = Column(String(255))
    author_email = Column(String(255))
    body = Column(Text, nullable=False)
    body_html = Column(Text)
    is_internal = Column(Boolean, default=False)  # internal note vs public reply
    attachments = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("Ticket", back_populates="comments")


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # created, status_changed, assigned, priority_changed, tag_added, commented, sla_breach
    field_name = Column(String(50))
    old_value = Column(Text)
    new_value = Column(Text)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_type = Column(String(20), default="system")  # user, system, trigger, ai
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    ticket = relationship("Ticket", back_populates="events")


# ============================================================
# SLA POLICIES
# ============================================================

class SLAPolicy(Base):
    __tablename__ = "sla_policies"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    is_default = Column(Boolean, default=False)

    # Targets in minutes per priority
    # Format: {"urgent": {"first_response": 30, "resolution": 240}, "high": {...}, ...}
    targets = Column(JSON, nullable=False)

    # Business hours
    # Format: {"timezone": "Asia/Kolkata", "schedule": {"mon": ["09:00", "17:00"], ...}}
    business_hours = Column(JSON)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# TRIGGERS & MACROS
# ============================================================

class Trigger(Base):
    __tablename__ = "triggers"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # When to fire: "ticket_created", "ticket_updated"
    event = Column(String(50), nullable=False)

    # Conditions: {"all": [...], "any": [...]}
    # Each condition: {"field": "status", "operator": "is", "value": "new"}
    conditions = Column(JSON, nullable=False, default=dict)

    # Actions: [{"type": "set_priority", "value": "high"}, {"type": "add_tag", "value": "billing"}]
    actions = Column(JSON, nullable=False, default=list)

    is_active = Column(Boolean, default=True)
    position = Column(Integer, default=0)  # execution order
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Macro(Base):
    __tablename__ = "macros"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)

    # Actions: [{"type": "set_status", "value": "pending"}, {"type": "add_comment", "value": "..."}]
    actions = Column(JSON, nullable=False, default=list)

    # Usage tracking
    usage_count = Column(Integer, default=0)
    last_used_at = Column(DateTime(timezone=True))

    created_by = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
    tickets_deflected = Column(Integer, default=0)

    # Source tracking (if auto-generated from ticket)
    source_ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=True)

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


# ============================================================
# WIDGET TICKETS (existing, now linked to main ticket system)
# ============================================================

class WidgetTicket(Base):
    __tablename__ = "widget_tickets"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String(100), nullable=False, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=True)  # link to main ticket

    subject = Column(String(500))
    message = Column(Text)
    customer_name = Column(String(255))
    customer_email = Column(String(255))

    status = Column(String(30), default="pending")
    ai_response = Column(Text)
    confidence = Column(Float)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# TICKET COUNTER (per company auto-increment)
# ============================================================

class TicketCounter(Base):
    __tablename__ = "ticket_counters"

    company_id = Column(String(100), primary_key=True)
    last_number = Column(Integer, default=0)
