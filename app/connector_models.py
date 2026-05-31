"""
connector_models.py
===================
Persistence for the connector framework:

    connector_integrations  one per (company, connector_type): tokens, config,
                            sync watermark, status.
    synced_documents        dedup/change-tracking per source document, and the
                            link from a source doc to its knowledge_chunks
                            (via source_id == synced_documents.id).
    connector_sync_runs     observability: one row per sync.

These reuse the existing KRAB Base. `synced_documents.id` is used as the
`source_id` on the chunks a document produces, so a document's chunks can be
found and replaced atomically on re-sync — no schema change to knowledge_chunks.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import relationship

try:
    from app.database import Base  # type: ignore
except Exception:
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

JSON_DOC = PG_JSONB().with_variant(JSON(), "sqlite")


class IntegrationStatus(str, enum.Enum):
    pending = "pending"          # OAuth started, awaiting callback
    active = "active"            # connected, syncing
    error = "error"             # last sync/connection failed
    disconnected = "disconnected"


class SyncRunStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


class ConnectorIntegration(Base):
    __tablename__ = "connector_integrations"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)
    connector_type = Column(String, index=True, nullable=False)  # "notion", "slack"

    display_name = Column(String, nullable=True)  # workspace/team name for the UI
    status = Column(
        Enum(IntegrationStatus, name="integration_status"),
        default=IntegrationStatus.pending,
        nullable=False,
        index=True,
    )

    # Secrets: encrypted at rest via crypto.py (never plaintext in prod).
    access_token_enc = Column(Text, nullable=True)
    refresh_token_enc = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Non-secret settings (channel filters, etc.). OAuth client secrets are NOT
    # stored here — they live in env and are injected at runtime.
    config = Column(JSON_DOC, default=dict)

    # CSRF/anti-forgery value for the OAuth round trip.
    oauth_state = Column(String, index=True, nullable=True)

    # Incremental-sync watermark + last outcome.
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_status = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    sync_interval_minutes = Column(Integer, default=360, nullable=False)  # 6h

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id", "connector_type", name="uq_integration_company_type"
        ),
    )


class SyncedDocument(Base):
    __tablename__ = "synced_documents"

    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(
        Integer, ForeignKey("connector_integrations.id", ondelete="CASCADE"), index=True
    )
    company_id = Column(String, index=True, nullable=False)
    connector_type = Column(String, index=True, nullable=False)

    external_id = Column(String, nullable=False)   # ID in the source system
    content_hash = Column(String, nullable=False)  # for change detection
    title = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)

    chunk_count = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=True)   # source's mtime
    synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "integration_id", "external_id", name="uq_synced_doc_ext"
        ),
    )


class ConnectorSyncRun(Base):
    __tablename__ = "connector_sync_runs"

    id = Column(Integer, primary_key=True)
    integration_id = Column(
        Integer, ForeignKey("connector_integrations.id", ondelete="CASCADE"), index=True
    )
    company_id = Column(String, index=True, nullable=False)

    status = Column(
        Enum(SyncRunStatus, name="sync_run_status"),
        default=SyncRunStatus.running,
        nullable=False,
    )
    documents_fetched = Column(Integer, default=0)
    documents_new = Column(Integer, default=0)
    documents_updated = Column(Integer, default=0)
    documents_unchanged = Column(Integer, default=0)
    chunks_created = Column(Integer, default=0)
    chunks_deleted = Column(Integer, default=0)
    triggered_resynthesis = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #
from pydantic import BaseModel, Field

_P2 = hasattr(BaseModel, "model_config")


class ConnectRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)  # e.g. {"channels": [...]}


class IntegrationOut(BaseModel):
    id: int
    connector_type: str
    display_name: Optional[str] = None
    status: str
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    sync_interval_minutes: int
    config: Dict[str, Any] = Field(default_factory=dict)

    if _P2:
        model_config = {"from_attributes": True}
    else:  # pragma: no cover
        class Config:
            orm_mode = True


class SyncRunOut(BaseModel):
    id: int
    status: str
    documents_fetched: int
    documents_new: int
    documents_updated: int
    documents_unchanged: int
    chunks_created: int
    chunks_deleted: int
    triggered_resynthesis: bool
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    if _P2:
        model_config = {"from_attributes": True}
    else:  # pragma: no cover
        class Config:
            orm_mode = True
