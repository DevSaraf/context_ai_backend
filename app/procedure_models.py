"""
procedure_models.py
===================
The data layer for KRAB's Company Brain.

Where `knowledge_chunks` stores RAW text (what a document SAYS), this module
stores STRUCTURED PROCEDURES (how the company WORKS). A Procedure is the atomic
unit an AI agent can execute: a trigger, ordered steps, decision rules, hard
guardrails, full provenance back to source chunks, and a confidence/autonomy
gate that decides whether an agent may run it unsupervised.

These ORM classes are designed to bolt onto the EXISTING KRAB Base/engine in
`database.py`. Import order at app startup must ensure `models.py` (which
defines `knowledge_chunks`) is imported before/alongside this file so the
ForeignKey to knowledge_chunks resolves.

Tables added:
    procedures            one row per executable procedure (current version)
    procedure_steps       ordered steps belonging to a procedure
    procedure_rules       decision rules / required inputs / guardrails
    procedure_sources     provenance: which chunks a procedure was built from
    procedure_versions    immutable JSON snapshots for audit / rollback
    knowledge_conflicts   contradictions detected across sources
    synthesis_runs        observability: one row per synthesis batch
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import relationship

# Use Postgres-native ARRAY/JSONB in production (indexable, efficient) but fall
# back to generic JSON on other backends (e.g. SQLite in unit tests). The app
# runs on Postgres + pgvector, so the PG variants are what actually ship.
INT_ARRAY = PG_ARRAY(Integer).with_variant(JSON(), "sqlite")
JSON_DOC = PG_JSONB().with_variant(JSON(), "sqlite")

# IMPORTANT: reuse the project's existing declarative Base so these tables
# register on the same metadata/engine as users + knowledge_chunks.
try:
    from app.database import Base  # type: ignore
except Exception:  # pragma: no cover - fallback for standalone import/testing
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class ProcedureStatus(str, enum.Enum):
    """Lifecycle of a procedure."""

    draft = "draft"               # freshly synthesized, not yet usable
    needs_review = "needs_review" # low confidence or has open conflicts
    verified = "verified"         # a human confirmed it is correct
    stale = "stale"               # sources changed; needs re-synthesis
    archived = "archived"         # superseded by a newer version


class AutonomyLevel(str, enum.Enum):
    """How much an agent is allowed to do with this procedure.

    This is the safety primitive the YC RFS calls out: agents must act
    "safely and consistently". An agent SDK should read this field and refuse
    to auto-execute anything below `autonomous`.
    """

    human_required = "human_required"  # surface to a human; do not act
    suggest_only = "suggest_only"      # draft an action, require approval
    autonomous = "autonomous"          # safe to execute unattended


class RuleType(str, enum.Enum):
    decision = "decision"           # "Refunds over $500 require manager sign-off"
    guardrail = "guardrail"         # "NEVER refund without an order ID"
    required_input = "required_input"  # "order_id"


class ConflictType(str, enum.Enum):
    value_mismatch = "value_mismatch"   # 30 days vs 14 days
    contradiction = "contradiction"     # "always X" vs "never X"
    outdated = "outdated"               # one source supersedes another
    ambiguous = "ambiguous"


class ConflictStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class RunStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


# --------------------------------------------------------------------------- #
# Core tables
# --------------------------------------------------------------------------- #
class Procedure(Base):
    __tablename__ = "procedures"

    id = Column(Integer, primary_key=True, index=True)

    # Multi-tenancy. EVERY read path must filter by one of these. Mirrors the
    # isolation contract already enforced in hybrid_search / widget endpoints.
    company_id = Column(String, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)

    # Identity. slug is stable across versions; (company_id, slug) is unique.
    slug = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)        # the agent-facing "use when"
    domain = Column(String, index=True, nullable=True)  # support | billing | eng ...
    trigger_condition = Column(Text, nullable=True)

    status = Column(
        Enum(ProcedureStatus, name="procedure_status"),
        default=ProcedureStatus.draft,
        nullable=False,
        index=True,
    )
    autonomy_level = Column(
        Enum(AutonomyLevel, name="autonomy_level"),
        default=AutonomyLevel.human_required,
        nullable=False,
        index=True,
    )

    version = Column(Integer, default=1, nullable=False)

    # Confidence / quality signals (all 0..1).
    synthesis_confidence = Column(Float, default=0.0, nullable=False)
    coverage_score = Column(Float, default=0.0, nullable=False)
    cohesion_score = Column(Float, default=0.0, nullable=False)

    # Execution feedback loop (the moat). Updated by record_execution_feedback().
    execution_count = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)

    # Change detection: stable hash of the source chunk set. If the underlying
    # chunks change, the signature changes and the procedure goes stale.
    cluster_signature = Column(String, index=True, nullable=True)

    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    steps = relationship(
        "ProcedureStep",
        back_populates="procedure",
        cascade="all, delete-orphan",
        order_by="ProcedureStep.step_order",
    )
    rules = relationship(
        "ProcedureRule", back_populates="procedure", cascade="all, delete-orphan"
    )
    sources = relationship(
        "ProcedureSource", back_populates="procedure", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_procedure_company_slug"),
        Index("ix_proc_company_status", "company_id", "status"),
    )

    # -- convenience -------------------------------------------------------- #
    @property
    def success_rate(self) -> Optional[float]:
        if self.execution_count == 0:
            return None
        return self.success_count / self.execution_count


class ProcedureStep(Base):
    __tablename__ = "procedure_steps"

    id = Column(Integer, primary_key=True)
    procedure_id = Column(
        Integer, ForeignKey("procedures.id", ondelete="CASCADE"), index=True
    )
    step_order = Column(Integer, nullable=False)
    instruction = Column(Text, nullable=False)
    # IDs of knowledge_chunks that justify this step (provenance per step).
    source_chunk_ids = Column(INT_ARRAY, default=list)

    procedure = relationship("Procedure", back_populates="steps")


class ProcedureRule(Base):
    __tablename__ = "procedure_rules"

    id = Column(Integer, primary_key=True)
    procedure_id = Column(
        Integer, ForeignKey("procedures.id", ondelete="CASCADE"), index=True
    )
    rule_type = Column(Enum(RuleType, name="rule_type"), nullable=False)
    content = Column(Text, nullable=False)
    source_chunk_ids = Column(INT_ARRAY, default=list)

    procedure = relationship("Procedure", back_populates="rules")


class ProcedureSource(Base):
    __tablename__ = "procedure_sources"

    id = Column(Integer, primary_key=True)
    procedure_id = Column(
        Integer, ForeignKey("procedures.id", ondelete="CASCADE"), index=True
    )
    # FK to the EXISTING knowledge_chunks table from models.py.
    chunk_id = Column(
        Integer, ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), index=True
    )
    relevance = Column(Float, default=0.0)
    contributed_at = Column(DateTime(timezone=True), server_default=func.now())

    procedure = relationship("Procedure", back_populates="sources")

    __table_args__ = (
        UniqueConstraint("procedure_id", "chunk_id", name="uq_proc_source"),
    )


class ProcedureVersion(Base):
    """Immutable snapshot taken each time a procedure is (re)synthesized.

    Enables audit ("what did the refund policy say in March?") and rollback.
    The snapshot is the full serialized procedure as JSON.
    """

    __tablename__ = "procedure_versions"

    id = Column(Integer, primary_key=True)
    procedure_id = Column(
        Integer, ForeignKey("procedures.id", ondelete="CASCADE"), index=True
    )
    company_id = Column(String, index=True, nullable=False)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON_DOC, nullable=False)
    cluster_signature = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("procedure_id", "version", name="uq_proc_version"),
    )


class KnowledgeConflict(Base):
    """A detected contradiction between sources.

    Surfacing conflicts (rather than silently picking one value) is a core
    feature: an enterprise needs to know its docs disagree before an agent
    acts on the wrong one.
    """

    __tablename__ = "knowledge_conflicts"

    id = Column(Integer, primary_key=True)
    company_id = Column(String, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)
    procedure_id = Column(
        Integer, ForeignKey("procedures.id", ondelete="SET NULL"), nullable=True
    )

    description = Column(Text, nullable=False)
    conflict_type = Column(
        Enum(ConflictType, name="conflict_type"),
        default=ConflictType.ambiguous,
        nullable=False,
    )
    chunk_id_a = Column(Integer, nullable=True)
    chunk_id_b = Column(Integer, nullable=True)

    status = Column(
        Enum(ConflictStatus, name="conflict_status"),
        default=ConflictStatus.open,
        nullable=False,
        index=True,
    )
    resolution_note = Column(Text, nullable=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class SynthesisRun(Base):
    """One row per synthesis batch, for cost/latency observability."""

    __tablename__ = "synthesis_runs"

    id = Column(Integer, primary_key=True)
    company_id = Column(String, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)

    status = Column(
        Enum(RunStatus, name="run_status"), default=RunStatus.running, nullable=False
    )
    chunks_considered = Column(Integer, default=0)
    clusters_found = Column(Integer, default=0)
    procedures_created = Column(Integer, default=0)
    procedures_updated = Column(Integer, default=0)
    procedures_unchanged = Column(Integer, default=0)
    conflicts_found = Column(Integer, default=0)
    llm_calls = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


class CompiledSkill(Base):
    """A verified procedure compiled into an executable SKILL.md body.

    One row per (company, slug). Recompiled in place each time the source
    procedure is re-verified or manually recompiled; `version` tracks how
    many times this skill has been (re)built.

    autonomy_level is stored as a plain String — it is a snapshot of the
    procedure's autonomy at compile time, not a live lifecycle field, so it
    intentionally does not use the Enum column type (avoids duplicate-type
    errors on Postgres create_all).
    """

    __tablename__ = "compiled_skills"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(String, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)

    procedure_id = Column(
        Integer, ForeignKey("procedures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    procedure = relationship("Procedure")

    slug = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False, default="")
    description = Column(Text, nullable=False, default="")

    skill_md = Column(Text, nullable=False, default="")

    autonomy_level = Column(String, nullable=False, default="suggest_only")

    version = Column(Integer, default=1, nullable=False)
    source_procedure_version = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    compiled_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_compiled_skill_company_slug"),
        Index("ix_compiled_skill_company", "company_id"),
    )


# --------------------------------------------------------------------------- #
# Pydantic schemas (API surface). Pydantic v2 style with v1 fallback.
# --------------------------------------------------------------------------- #
try:
    from pydantic import BaseModel, Field

    _P2 = hasattr(BaseModel, "model_config")
except Exception:  # pragma: no cover
    raise


class StepOut(BaseModel):
    step_order: int
    instruction: str
    source_chunk_ids: List[int] = Field(default_factory=list)


class RuleOut(BaseModel):
    rule_type: str
    content: str
    source_chunk_ids: List[int] = Field(default_factory=list)


class ProcedureOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    domain: Optional[str] = None
    trigger_condition: Optional[str] = None
    status: str
    autonomy_level: str
    version: int
    synthesis_confidence: float
    coverage_score: float
    cohesion_score: float
    execution_count: int
    success_count: int
    success_rate: Optional[float] = None
    last_verified_at: Optional[datetime] = None
    steps: List[StepOut] = Field(default_factory=list)
    rules: List[RuleOut] = Field(default_factory=list)
    source_chunk_ids: List[int] = Field(default_factory=list)

    if _P2:
        model_config = {"from_attributes": True}
    else:  # pragma: no cover
        class Config:
            orm_mode = True


class ConflictOut(BaseModel):
    id: int
    description: str
    conflict_type: str
    status: str
    chunk_id_a: Optional[int] = None
    chunk_id_b: Optional[int] = None
    procedure_id: Optional[int] = None
    detected_at: Optional[datetime] = None

    if _P2:
        model_config = {"from_attributes": True}
    else:  # pragma: no cover
        class Config:
            orm_mode = True


class SynthesisRunOut(BaseModel):
    id: int
    status: str
    chunks_considered: int
    clusters_found: int
    procedures_created: int
    procedures_updated: int
    procedures_unchanged: int
    conflicts_found: int
    llm_calls: int
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    if _P2:
        model_config = {"from_attributes": True}
    else:  # pragma: no cover
        class Config:
            orm_mode = True


class SynthesizeRequest(BaseModel):
    domain: Optional[str] = Field(
        default=None, description="Restrict synthesis to one domain tag."
    )
    force: bool = Field(
        default=False,
        description="Re-synthesize even procedures whose sources are unchanged.",
    )
    min_cluster_size: int = Field(default=3, ge=2, le=50)


class ExecutionFeedback(BaseModel):
    procedure_id: int
    success: bool
    note: Optional[str] = None


class VerifyRequest(BaseModel):
    approve: bool = True
    note: Optional[str] = None
