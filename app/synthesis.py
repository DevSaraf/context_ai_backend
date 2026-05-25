"""
synthesis.py
============
The orchestrator that turns raw `knowledge_chunks` into structured, versioned,
confidence-gated `procedures` -- KRAB's Company Brain.

Pipeline (run_synthesis):
    1. Load this tenant's chunks (+ embeddings + resolution_score).
    2. Cluster embeddings into candidate topics (clustering.py).
    3. For each cluster, call the LLM to extract ONE structured Procedure
       (synthesis_prompts.py). Concurrency-limited with retries.
    4. Score it: synthesis_confidence from LLM confidence + source quality +
       cohesion + coverage, penalized by conflicts.
    5. Gate it: derive an AutonomyLevel and ProcedureStatus from the score and
       whether conflicts are open.
    6. Persist with provenance + a JSON version snapshot; skip unchanged
       procedures (stable cluster_signature) unless force=True.

Integration points you must wire (see README_company_brain.md):
    * `_load_chunks` reads your existing knowledge_chunks columns.
    * `llm_complete` -> route to your existing llm_provider.py. A working
      default (Gemini REST, matching your rag_engine) is provided.

This module is async to match the existing FastAPI codebase and runs the LLM
calls concurrently under a semaphore.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.clustering import cluster_cohesion, cluster_embeddings
from app.procedure_models import (
    AutonomyLevel,
    ConflictStatus,
    ConflictType,
    KnowledgeConflict,
    Procedure,
    ProcedureRule,
    ProcedureSource,
    ProcedureStatus,
    ProcedureStep,
    ProcedureVersion,
    RuleType,
    RunStatus,
    SynthesisRun,
)
from app.synthesis_prompts import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt,
)

logger = logging.getLogger("krab.brain.synthesis")

# --- tunables (override via env in production) ----------------------------- #
MAX_CONCURRENT_LLM = int(os.getenv("BRAIN_LLM_CONCURRENCY", "4"))
MAX_CHUNKS_PER_CLUSTER = int(os.getenv("BRAIN_MAX_CHUNKS_PER_CLUSTER", "12"))
LLM_MAX_RETRIES = int(os.getenv("BRAIN_LLM_RETRIES", "3"))
COVERAGE_TARGET = int(os.getenv("BRAIN_COVERAGE_TARGET", "5"))  # chunks for full coverage

# Confidence weights (must sum to 1.0).
W_LLM = 0.35
W_SOURCE = 0.25
W_COHESION = 0.20
W_COVERAGE = 0.20

# Autonomy gate thresholds.
AUTONOMOUS_MIN_CONF = 0.78
AUTONOMOUS_MIN_EXEC = 10
AUTONOMOUS_MIN_SUCCESS = 0.9
SUGGEST_MIN_CONF = 0.55


# --------------------------------------------------------------------------- #
# LLM adapter
# --------------------------------------------------------------------------- #
from app.llm_provider import llm

async def llm_complete(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
) -> str:
    """Return the model's text completion."""
    return await llm.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens
    )


def _parse_json(raw: str) -> Dict[str, Any]:
    """Defensive JSON parse: strip code fences / leading prose if present."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # last resort: grab the outermost {...}
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(s[start : end + 1])
        raise


# --------------------------------------------------------------------------- #
# In-memory chunk representation
# --------------------------------------------------------------------------- #
@dataclass
class _Chunk:
    id: int
    text: str
    embedding: List[float]
    resolution_score: float = 0.5
    source_type: str = ""


@dataclass
class _Extracted:
    """Result of one LLM extraction, mapped back to real chunk ids."""

    data: Dict[str, Any]
    cluster_chunk_ids: List[int]
    cohesion: float


# --------------------------------------------------------------------------- #
# Loading (integration point #1)
# --------------------------------------------------------------------------- #
def _load_chunks(
    db: Session, *, company_id: str, user_id: int, domain: Optional[str]
) -> List[_Chunk]:
    """Load chunks + embeddings for one tenant.

    Uses raw SQL because pgvector columns map cleanly to a string we parse, and
    it avoids assumptions about your ORM column name for the vector. Adjust the
    column list if your knowledge_chunks schema differs from the architecture
    doc (id, user_id, company_id, text, embedding, resolution_score,
    source_type).
    """
    params: Dict[str, Any] = {"user_id": user_id}
    where = "user_id = :user_id"
    if domain:
        # If you tag chunks by domain, filter here. Otherwise this is a no-op
        # because source_type is matched loosely.
        where += " AND source_type ILIKE :domain"
        params["domain"] = f"%{domain}%"

    rows = db.execute(
        sql_text(
            f"""
            SELECT id, text,
                   embedding::text AS emb,
                   COALESCE(resolution_score, 0.5) AS resolution_score,
                   COALESCE(source_type, '') AS source_type
            FROM knowledge_chunks
            WHERE {where}
            """
        ),
        params,
    ).fetchall()

    chunks: List[_Chunk] = []
    for r in rows:
        emb = _parse_vector(r.emb)
        if emb is None:
            continue
        chunks.append(
            _Chunk(
                id=int(r.id),
                text=r.text or "",
                embedding=emb,
                resolution_score=float(r.resolution_score),
                source_type=r.source_type or "",
            )
        )
    return chunks


def _parse_vector(raw: Optional[str]) -> Optional[List[float]]:
    """pgvector serializes as '[0.1,0.2,...]'. Parse to a float list."""
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        return [float(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        return None


def _signature(chunk_ids: Sequence[int]) -> str:
    """Stable hash of a cluster's source set, for change detection."""
    joined = ",".join(str(c) for c in sorted(chunk_ids))
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Scoring + gating
# --------------------------------------------------------------------------- #
def _compute_confidence(
    *, llm_conf: float, source_quality: float, cohesion: float, coverage: float,
    num_conflicts: int,
) -> float:
    base = (
        W_LLM * _clamp(llm_conf)
        + W_SOURCE * _clamp(source_quality)
        + W_COHESION * _clamp(cohesion)
        + W_COVERAGE * _clamp(coverage)
    )
    # Each open conflict erodes trust.
    base *= max(0.0, 1.0 - 0.1 * num_conflicts)
    return round(_clamp(base), 4)


def _derive_autonomy(
    confidence: float, execution_count: int, success_rate: Optional[float],
    has_open_conflict: bool,
) -> AutonomyLevel:
    """The safety gate. Conflicts always force a human into the loop."""
    if has_open_conflict or confidence < SUGGEST_MIN_CONF:
        return AutonomyLevel.human_required
    # Proven by execution history -> fully autonomous.
    if (
        execution_count >= AUTONOMOUS_MIN_EXEC
        and success_rate is not None
        and success_rate >= AUTONOMOUS_MIN_SUCCESS
    ):
        return AutonomyLevel.autonomous
    # High synthesis confidence but not yet battle-tested -> suggest only.
    if confidence >= AUTONOMOUS_MIN_CONF:
        return AutonomyLevel.suggest_only
    return AutonomyLevel.suggest_only if confidence >= SUGGEST_MIN_CONF else AutonomyLevel.human_required


def _derive_status(autonomy: AutonomyLevel, has_open_conflict: bool) -> ProcedureStatus:
    if has_open_conflict or autonomy == AutonomyLevel.human_required:
        return ProcedureStatus.needs_review
    return ProcedureStatus.draft


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# --------------------------------------------------------------------------- #
# Extraction (one cluster -> one procedure dict)
# --------------------------------------------------------------------------- #
async def _extract_cluster(
    chunks: List[_Chunk], semaphore: asyncio.Semaphore
) -> Optional[_Extracted]:
    # Cap chunks sent to the LLM (cost) -- prefer the highest-quality ones.
    ordered = sorted(chunks, key=lambda c: c.resolution_score, reverse=True)
    selected = ordered[:MAX_CHUNKS_PER_CLUSTER]
    texts = [c.text for c in selected]
    cohesion = cluster_cohesion([c.embedding for c in selected])

    user_prompt = build_extraction_user_prompt(texts)
    async with semaphore:
        raw = await _with_retries(
            lambda: llm_complete(EXTRACTION_SYSTEM_PROMPT, user_prompt, max_tokens=1800)
        )
    try:
        data = _parse_json(raw)
    except Exception as exc:
        logger.warning("extraction JSON parse failed: %s", exc)
        return None

    if not data.get("is_procedure"):
        return None

    # Map source_hints (batch indices) -> real chunk ids.
    data["_index_to_chunk_id"] = {i: selected[i].id for i in range(len(selected))}
    return _Extracted(
        data=data,
        cluster_chunk_ids=[c.id for c in selected],
        cohesion=cohesion,
    )


async def _with_retries(coro_factory):
    delay = 1.0
    last: Optional[Exception] = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            last = exc
            logger.warning("LLM call failed (attempt %d): %s", attempt + 1, exc)
            await asyncio.sleep(delay)
            delay *= 2
    raise last  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _hints_to_ids(hints: Any, index_map: Dict[int, int]) -> List[int]:
    out: List[int] = []
    if isinstance(hints, list):
        for h in hints:
            try:
                cid = index_map.get(int(h))
            except (TypeError, ValueError):
                cid = None
            if cid is not None:
                out.append(cid)
    return out


def _persist_procedure(
    db: Session,
    *,
    company_id: str,
    user_id: int,
    extracted: _Extracted,
    chunk_by_id: Dict[int, _Chunk],
    force: bool,
    run: SynthesisRun,
) -> None:
    data = extracted.data
    index_map = data.get("_index_to_chunk_id", {})
    slug = _safe_slug(data.get("slug") or data.get("title") or "procedure")
    signature = _signature(extracted.cluster_chunk_ids)

    existing: Optional[Procedure] = (
        db.query(Procedure)
        .filter(Procedure.company_id == company_id, Procedure.slug == slug)
        .first()
    )

    # Unchanged source set and not forced -> just re-affirm freshness.
    if existing and existing.cluster_signature == signature and not force:
        existing.last_verified_at = datetime.now(timezone.utc)
        existing.status = (
            ProcedureStatus.stale.value  # type: ignore[assignment]
            if False
            else existing.status
        )
        run.procedures_unchanged += 1
        db.commit()
        return

    # Build conflicts first; they affect scoring + gating.
    raw_conflicts = data.get("internal_conflicts") or []
    conflict_rows: List[KnowledgeConflict] = []
    for c in raw_conflicts:
        conflict_rows.append(
            KnowledgeConflict(
                company_id=company_id,
                user_id=user_id,
                description=str(c.get("description", "")),
                conflict_type=_safe_conflict_type(c.get("type")),
                chunk_id_a=index_map.get(_as_int(c.get("source_a"))),
                chunk_id_b=index_map.get(_as_int(c.get("source_b"))),
                status=ConflictStatus.open,
            )
        )

    source_quality = (
        sum(chunk_by_id[cid].resolution_score for cid in extracted.cluster_chunk_ids)
        / max(1, len(extracted.cluster_chunk_ids))
    )
    coverage = min(1.0, len(extracted.cluster_chunk_ids) / COVERAGE_TARGET)
    confidence = _compute_confidence(
        llm_conf=float(data.get("extraction_confidence", 0.5)),
        source_quality=source_quality,
        cohesion=extracted.cohesion,
        coverage=coverage,
        num_conflicts=len(conflict_rows),
    )

    # Preserve execution history across re-synthesis.
    prev_exec = existing.execution_count if existing else 0
    prev_succ = existing.success_count if existing else 0
    succ_rate = (prev_succ / prev_exec) if prev_exec else None
    autonomy = _derive_autonomy(confidence, prev_exec, succ_rate, bool(conflict_rows))
    status = _derive_status(autonomy, bool(conflict_rows))

    if existing:
        proc = existing
        proc.version += 1
        # wipe child rows; cascade handles deletes on flush
        proc.steps.clear()
        proc.rules.clear()
        proc.sources.clear()
        run.procedures_updated += 1
    else:
        proc = Procedure(company_id=company_id, user_id=user_id, slug=slug, version=1)
        db.add(proc)
        run.procedures_created += 1

    proc.title = str(data.get("title", slug))
    proc.description = str(data.get("description", ""))
    proc.domain = (data.get("domain") or None)
    proc.trigger_condition = data.get("trigger_condition")
    proc.synthesis_confidence = confidence
    proc.coverage_score = round(coverage, 4)
    proc.cohesion_score = round(extracted.cohesion, 4)
    proc.cluster_signature = signature
    proc.execution_count = prev_exec
    proc.success_count = prev_succ
    proc.autonomy_level = autonomy
    proc.status = status
    proc.last_verified_at = None  # re-synthesis requires fresh human verify

    db.flush()  # assign proc.id

    for i, step in enumerate(data.get("steps") or []):
        db.add(
            ProcedureStep(
                procedure_id=proc.id,
                step_order=i,
                instruction=str(step.get("instruction", "")).strip(),
                source_chunk_ids=_hints_to_ids(step.get("source_hints"), index_map),
            )
        )

    for rule in data.get("decision_rules") or []:
        db.add(_rule_row(proc.id, RuleType.decision, rule, index_map))
    for guard in data.get("guardrails") or []:
        db.add(_rule_row(proc.id, RuleType.guardrail, guard, index_map))
    for inp in data.get("required_inputs") or []:
        db.add(
            ProcedureRule(
                procedure_id=proc.id,
                rule_type=RuleType.required_input,
                content=str(inp),
                source_chunk_ids=[],
            )
        )

    for cid in extracted.cluster_chunk_ids:
        db.add(
            ProcedureSource(
                procedure_id=proc.id,
                chunk_id=cid,
                relevance=chunk_by_id[cid].resolution_score,
            )
        )

    for cr in conflict_rows:
        cr.procedure_id = proc.id
        db.add(cr)
    run.conflicts_found += len(conflict_rows)

    db.flush()
    _snapshot(db, proc, company_id, signature)
    db.commit()


def _rule_row(proc_id: int, rtype: RuleType, rule: Any, index_map: Dict[int, int]) -> ProcedureRule:
    if isinstance(rule, dict):
        content = str(rule.get("content", "")).strip()
        hints = rule.get("source_hints")
    else:
        content = str(rule).strip()
        hints = None
    return ProcedureRule(
        procedure_id=proc_id,
        rule_type=rtype,
        content=content,
        source_chunk_ids=_hints_to_ids(hints, index_map),
    )


def _snapshot(db: Session, proc: Procedure, company_id: str, signature: str) -> None:
    snap = {
        "title": proc.title,
        "description": proc.description,
        "domain": proc.domain,
        "trigger_condition": proc.trigger_condition,
        "autonomy_level": proc.autonomy_level.value
        if isinstance(proc.autonomy_level, AutonomyLevel)
        else proc.autonomy_level,
        "synthesis_confidence": proc.synthesis_confidence,
        "steps": [
            {"order": s.step_order, "instruction": s.instruction,
             "sources": s.source_chunk_ids}
            for s in sorted(proc.steps, key=lambda x: x.step_order)
        ],
        "rules": [
            {"type": r.rule_type.value if isinstance(r.rule_type, RuleType) else r.rule_type,
             "content": r.content, "sources": r.source_chunk_ids}
            for r in proc.rules
        ],
    }
    db.add(
        ProcedureVersion(
            procedure_id=proc.id,
            company_id=company_id,
            version=proc.version,
            snapshot=snap,
            cluster_signature=signature,
        )
    )


def _safe_slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s or "procedure")[:80]


def _safe_conflict_type(v: Any) -> ConflictType:
    try:
        return ConflictType(str(v))
    except ValueError:
        return ConflictType.ambiguous


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
async def run_synthesis(
    db: Session,
    *,
    company_id: str,
    user_id: int,
    domain: Optional[str] = None,
    force: bool = False,
    min_cluster_size: int = 3,
    run_id: Optional[int] = None,
) -> SynthesisRun:
    """Synthesize procedures for one tenant. Returns the SynthesisRun row."""
    if run_id:
        run = db.query(SynthesisRun).filter(SynthesisRun.id == run_id).first()
        if not run:
            raise ValueError(f"Run {run_id} not found")
        run.status = RunStatus.running
    else:
        run = SynthesisRun(company_id=company_id, user_id=user_id, status=RunStatus.running)
        db.add(run)
    db.commit()
    db.refresh(run)

    try:
        chunks = _load_chunks(db, company_id=company_id, user_id=user_id, domain=domain)
        run.chunks_considered = len(chunks)
        if not chunks:
            run.status = RunStatus.completed
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return run

        chunk_by_id = {c.id: c for c in chunks}
        clusters = cluster_embeddings(
            [(c.id, c.embedding) for c in chunks], min_cluster_size=min_cluster_size
        )
        run.clusters_found = len(clusters)
        db.commit()

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM)
        tasks = [
            _extract_cluster([chunk_by_id[cid] for cid in cids], semaphore)
            for cids in clusters.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            run.llm_calls += 1
            if isinstance(res, Exception):
                logger.error("cluster extraction error: %s", res)
                continue
            if res is None:
                continue
            try:
                _persist_procedure(
                    db,
                    company_id=company_id,
                    user_id=user_id,
                    extracted=res,
                    chunk_by_id=chunk_by_id,
                    force=force,
                    run=run,
                )
            except Exception as exc:  # one bad cluster shouldn't kill the run
                logger.exception("persist failed for a cluster: %s", exc)
                db.rollback()

        run.status = RunStatus.completed
    except Exception as exc:
        logger.exception("synthesis run failed")
        run.status = RunStatus.failed
        run.error = str(exc)[:2000]
    finally:
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    return run


# --------------------------------------------------------------------------- #
# Feedback loop (the moat): execution outcomes promote/demote autonomy
# --------------------------------------------------------------------------- #
def record_execution_feedback(
    db: Session, *, company_id: str, procedure_id: int, success: bool
) -> Procedure:
    """Record one real-world execution outcome and re-derive autonomy.

    This is what lets a procedure EARN the right to run unattended: once it has
    enough successful executions, _derive_autonomy promotes it to `autonomous`.
    A failure can pull it back down.
    """
    proc = (
        db.query(Procedure)
        .filter(Procedure.id == procedure_id, Procedure.company_id == company_id)
        .first()
    )
    if not proc:
        raise ValueError("procedure not found for this company")

    proc.execution_count += 1
    if success:
        proc.success_count += 1

    has_open_conflict = (
        db.query(KnowledgeConflict)
        .filter(
            KnowledgeConflict.procedure_id == proc.id,
            KnowledgeConflict.status == ConflictStatus.open,
        )
        .count()
        > 0
    )
    proc.autonomy_level = _derive_autonomy(
        proc.synthesis_confidence,
        proc.execution_count,
        proc.success_rate,
        has_open_conflict,
    )
    db.commit()
    db.refresh(proc)
    return proc
