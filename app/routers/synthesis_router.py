"""
routers/synthesis_router.py
===========================
HTTP surface for the Company Brain. Mirrors KRAB's existing router pattern:
JWT -> get_current_user -> user_id, then resolve the user's company_id.

Endpoints:
    POST /brain/synthesize              kick off a synthesis run (background)
    GET  /brain/runs/{run_id}           poll a run's status
    GET  /brain/procedures              list procedures (filter by status/domain)
    GET  /brain/procedures/{id}         full procedure with steps/rules/sources
    POST /brain/procedures/{id}/verify  human approves -> verified + autonomy bump
    POST /brain/feedback/execution      record an execution outcome (the moat)
    GET  /brain/conflicts               list open contradictions to resolve
    POST /brain/conflicts/{id}/resolve  mark a conflict resolved/dismissed

Wire into main.py:
    from routers import synthesis_router
    app.include_router(synthesis_router.router)
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

# Reuse the project's existing primitives.
from app.database import SessionLocal  # type: ignore
from app.dependencies import get_current_user  # type: ignore
from app.models import User  # type: ignore

from app.procedure_models import (
    AutonomyLevel,
    ConflictOut,
    ConflictStatus,
    ExecutionFeedback,
    KnowledgeConflict,
    Procedure,
    ProcedureOut,
    ProcedureStatus,
    RuleOut,
    StepOut,
    SynthesisRun,
    SynthesisRunOut,
    SynthesizeRequest,
    VerifyRequest,
)
from app.synthesis import record_execution_feedback, run_synthesis

router = APIRouter(prefix="/brain", tags=["company-brain"])


# Local DB dependency (same shape as the rest of KRAB).
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _serialize(proc: Procedure) -> ProcedureOut:
    return ProcedureOut(
        id=proc.id,
        slug=proc.slug,
        title=proc.title,
        description=proc.description,
        domain=proc.domain,
        trigger_condition=proc.trigger_condition,
        status=proc.status.value if hasattr(proc.status, "value") else proc.status,
        autonomy_level=proc.autonomy_level.value
        if hasattr(proc.autonomy_level, "value")
        else proc.autonomy_level,
        version=proc.version,
        synthesis_confidence=proc.synthesis_confidence,
        coverage_score=proc.coverage_score,
        cohesion_score=proc.cohesion_score,
        execution_count=proc.execution_count,
        success_count=proc.success_count,
        success_rate=proc.success_rate,
        last_verified_at=proc.last_verified_at,
        steps=[
            StepOut(
                step_order=s.step_order,
                instruction=s.instruction,
                source_chunk_ids=s.source_chunk_ids or [],
            )
            for s in sorted(proc.steps, key=lambda x: x.step_order)
        ],
        rules=[
            RuleOut(
                rule_type=r.rule_type.value if hasattr(r.rule_type, "value") else r.rule_type,
                content=r.content,
                source_chunk_ids=r.source_chunk_ids or [],
            )
            for r in proc.rules
        ],
        source_chunk_ids=[s.chunk_id for s in proc.sources],
    )


# --------------------------------------------------------------------------- #
# Synthesis Engine Trigger & Polling
# --------------------------------------------------------------------------- #
async def _run_in_background(
    company_id: str, user_id: int, req: SynthesizeRequest, run_id: int
):
    """Executes the heavy synthesis in a background task."""
    db = SessionLocal()
    try:
        await run_synthesis(
            db=db,
            company_id=company_id,
            user_id=user_id,
            domain=req.domain,
            force=req.force,
            min_cluster_size=req.min_cluster_size,
            run_id=run_id,
        )
    finally:
        db.close()


@router.post("/synthesize", response_model=SynthesisRunOut)
def synthesize(
    req: SynthesizeRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start a synthesis run. Returns immediately with a 'running' run row;
    poll GET /brain/runs/{id}. (Synthesis can take minutes for large corpora.)"""
    run = SynthesisRun(company_id=user.company_id, user_id=user.id)
    db.add(run)
    db.commit()
    db.refresh(run)
    # Hand off the actual work; reuse the same run_id.
    background.add_task(_run_in_background, user.company_id, user.id, req, run.id)
    return SynthesisRunOut.model_validate(run)


@router.get("/runs/{run_id}", response_model=SynthesisRunOut)
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = (
        db.query(SynthesisRun)
        .filter(SynthesisRun.id == run_id, SynthesisRun.company_id == user.company_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return SynthesisRunOut.model_validate(run)


# --------------------------------------------------------------------------- #
# Procedures
# --------------------------------------------------------------------------- #
@router.get("/procedures", response_model=List[ProcedureOut])
def list_procedures(
    status: Optional[str] = Query(default=None),
    domain: Optional[str] = Query(default=None),
    autonomy: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Procedure).filter(Procedure.company_id == user.company_id)
    if status:
        q = q.filter(Procedure.status == ProcedureStatus(status))
    if domain:
        q = q.filter(Procedure.domain == domain)
    if autonomy:
        q = q.filter(Procedure.autonomy_level == AutonomyLevel(autonomy))
    q = q.order_by(Procedure.synthesis_confidence.desc())
    return [_serialize(p) for p in q.all()]


@router.get("/procedures/{procedure_id}", response_model=ProcedureOut)
def get_procedure(
    procedure_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proc = (
        db.query(Procedure)
        .filter(Procedure.id == procedure_id, Procedure.company_id == user.company_id)
        .first()
    )
    if not proc:
        raise HTTPException(status_code=404, detail="Procedure not found")
    return _serialize(proc)


@router.post("/procedures/{procedure_id}/verify", response_model=ProcedureOut)
def verify_procedure(
    procedure_id: int,
    req: VerifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Human-in-the-loop approval. Approving marks it verified and, if no open
    conflicts remain, lets it begin earning autonomy through execution."""
    from datetime import datetime, timezone

    proc = (
        db.query(Procedure)
        .filter(Procedure.id == procedure_id, Procedure.company_id == user.company_id)
        .first()
    )
    if not proc:
        raise HTTPException(status_code=404, detail="Procedure not found")

    open_conflicts = (
        db.query(KnowledgeConflict)
        .filter(
            KnowledgeConflict.procedure_id == proc.id,
            KnowledgeConflict.status == ConflictStatus.open,
        )
        .count()
    )
    if req.approve and open_conflicts > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Resolve {open_conflicts} open conflict(s) before verifying.",
        )

    if req.approve:
        proc.status = ProcedureStatus.verified
        proc.last_verified_at = datetime.now(timezone.utc)
        # A verified, conflict-free procedure is at least suggest-ready.
        if proc.autonomy_level == AutonomyLevel.human_required:
            proc.autonomy_level = AutonomyLevel.suggest_only
    else:
        proc.status = ProcedureStatus.needs_review
    db.commit()
    db.refresh(proc)
    return _serialize(proc)


# --------------------------------------------------------------------------- #
# Feedback loop
# --------------------------------------------------------------------------- #
@router.post("/feedback/execution", response_model=ProcedureOut)
def execution_feedback(
    fb: ExecutionFeedback,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        proc = record_execution_feedback(
            db, company_id=user.company_id, procedure_id=fb.procedure_id, success=fb.success
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize(proc)


# --------------------------------------------------------------------------- #
# Conflicts
# --------------------------------------------------------------------------- #
@router.get("/conflicts", response_model=List[ConflictOut])
def list_conflicts(
    status: str = Query(default="open"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(KnowledgeConflict).filter(KnowledgeConflict.company_id == user.company_id)
    if status:
        q = q.filter(KnowledgeConflict.status == ConflictStatus(status))
    return [ConflictOut.model_validate(c) for c in q.order_by(KnowledgeConflict.detected_at.desc()).all()]


@router.post("/conflicts/{conflict_id}/resolve", response_model=ConflictOut)
def resolve_conflict(
    conflict_id: int,
    note: Optional[str] = None,
    dismiss: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from datetime import datetime, timezone

    c = (
        db.query(KnowledgeConflict)
        .filter(
            KnowledgeConflict.id == conflict_id,
            KnowledgeConflict.company_id == user.company_id,
        )
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="Conflict not found")
    c.status = ConflictStatus.dismissed if dismiss else ConflictStatus.resolved
    c.resolution_note = note
    c.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(c)
    return ConflictOut.model_validate(c)

# --------------------------------------------------------------------------- #
# Skills (Addons)
# --------------------------------------------------------------------------- #
@router.get("/skills")
def list_skills_rest(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Procedure).filter(Procedure.company_id == user.company_id)
    return {"skills": [
        {
            "name": p.slug,
            "description": p.intent,
            "autonomy_level": p.autonomy_level.value if hasattr(p.autonomy_level, "value") else p.autonomy_level,
            "success_rate": p.success_rate,
            "execution_count": p.execution_count,
        } for p in q.all()
    ]}


@router.get("/skills/{name}")
def get_skill_rest(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = db.query(Procedure).filter(
        Procedure.company_id == user.company_id, Procedure.slug == name
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {
        "name": p.slug,
        "description": p.intent,
        "autonomy_level": p.autonomy_level.value if hasattr(p.autonomy_level, "value") else p.autonomy_level,
        "success_rate": p.success_rate,
        "execution_count": p.execution_count,
        "steps": [s.instruction for s in sorted(p.steps, key=lambda x: x.step_order)],
        "rules": [r.content for r in p.rules]
    }
