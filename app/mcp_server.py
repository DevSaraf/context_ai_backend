"""
mcp_server.py
=============
Exposes KRAB's Company Brain to AI agents over the Model Context Protocol.
This is the "Software for Agents" interface: instead of a human pasting context
into a textarea, an agent calls these tools to discover, load, and execute the
company's procedures -- and reports back outcomes that drive the autonomy gate.

Tools exposed:
    list_skills(domain?, autonomy?)   -> compact catalog of available procedures
    search_skills(query, limit?)      -> hybrid search over procedures
    get_skill(name)                   -> full SKILL.md + machine-readable safety
    report_execution(name, success)   -> close the loop (promotes/demotes autonomy)

AUTH + TENANCY:
    Every request is scoped to ONE company. We reuse KRAB's existing per-company
    `api_key` (already on the User row). Two transports:
      * stdio   -> for Claude Code / local agents. api_key from KRAB_API_KEY env.
      * HTTP    -> for remote customer agents. api_key from `X-API-Key` header.

    The api_key resolves to (user_id, company_id) and EVERY query is filtered by
    company_id -- the same isolation contract as the widget endpoint.

Run:
    # stdio (Claude Code):  KRAB_API_KEY=... python mcp_server.py
    # http:                 python mcp_server.py --http --port 8077
"""

from __future__ import annotations

import argparse
import json
import os
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session

from app.database import SessionLocal  # type: ignore
from app.models import User  # type: ignore
from app.procedure_models import AutonomyLevel, Procedure, ProcedureStatus
from app.skill_compiler import compile_procedure_to_skill
from app.synthesis import record_execution_feedback

# Optional: reuse existing search for semantic skill discovery.
try:
    from app.embedding import create_embedding  # type: ignore
    from app.hybrid_search import hybrid_search  # type: ignore

    _HAS_SEARCH = True
except Exception:  # pragma: no cover
    _HAS_SEARCH = False


mcp = FastMCP(
    "krab-company-brain",
    instructions=(
        "The company's executable knowledge. Call list_skills or search_skills to "
        "find a procedure, get_skill to load it, then FOLLOW ITS AUTONOMY LEVEL: "
        "only auto-execute skills marked 'autonomous'; 'suggest_only' needs human "
        "approval; 'human_required' must be handed to a person. After executing a "
        "skill, call report_execution so the brain can learn from the outcome."
    ),
)

# Holds the api_key for the current request. For stdio it's process-wide (one
# tenant per process); for HTTP it's set per-request from the header.
_api_key_ctx: ContextVar[Optional[str]] = ContextVar("api_key", default=None)


# --------------------------------------------------------------------------- #
# Auth helper
# --------------------------------------------------------------------------- #
def _resolve_tenant(db: Session) -> User:
    api_key = _api_key_ctx.get() or os.getenv("KRAB_API_KEY")
    if not api_key:
        raise PermissionError(
            "No API key. Set KRAB_API_KEY (stdio) or send X-API-Key (http)."
        )
    user = db.query(User).filter(User.api_key == api_key).first()
    if not user:
        raise PermissionError("Invalid API key.")
    return user


def _autonomy_filter(q, autonomy: Optional[str]):
    if autonomy:
        q = q.filter(Procedure.autonomy_level == AutonomyLevel(autonomy))
    return q


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_skills(domain: Optional[str] = None, autonomy: Optional[str] = None) -> str:
    """List the company's available skills (procedures).

    Returns a compact JSON catalog: name, title, what it's for, autonomy level,
    confidence, and execution track record. Use this to see what the company can
    do before loading a specific skill. Optionally filter by `domain`
    (e.g. "support", "billing") or `autonomy`
    ("autonomous" | "suggest_only" | "human_required").
    """
    db = SessionLocal()
    try:
        user = _resolve_tenant(db)
        q = db.query(Procedure).filter(Procedure.company_id == user.company_id)
        # Don't advertise drafts/archived to agents -- only usable procedures.
        q = q.filter(
            Procedure.status.in_(
                [ProcedureStatus.verified, ProcedureStatus.draft, ProcedureStatus.needs_review]
            )
        )
        if domain:
            q = q.filter(Procedure.domain == domain)
        q = _autonomy_filter(q, autonomy)
        q = q.order_by(Procedure.synthesis_confidence.desc())

        catalog: List[Dict[str, Any]] = []
        for p in q.all():
            catalog.append(
                {
                    "name": p.slug,
                    "title": p.title,
                    "description": p.description,
                    "domain": p.domain,
                    "autonomy_level": _val(p.autonomy_level),
                    "status": _val(p.status),
                    "confidence": round(p.synthesis_confidence, 3),
                    "executions": p.execution_count,
                    "success_rate": round(p.success_rate, 3)
                    if p.success_rate is not None
                    else None,
                }
            )
        return json.dumps({"company": user.company_id, "skills": catalog}, indent=2)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def search_skills(query: str, limit: int = 5) -> str:
    """Find skills relevant to a task by meaning, not just keywords.

    Pass a natural-language description of what you're trying to do
    (e.g. "customer wants their money back" or "production database is down").
    Returns the best-matching procedures. Use this when you don't know the exact
    skill name. Falls back to keyword matching if semantic search is unavailable.
    """
    db = SessionLocal()
    try:
        user = _resolve_tenant(db)

        # Prefer semantic search over the procedures' descriptions/titles.
        procs = (
            db.query(Procedure)
            .filter(
                Procedure.company_id == user.company_id,
                Procedure.status.in_(
                    [ProcedureStatus.verified, ProcedureStatus.draft, ProcedureStatus.needs_review]
                ),
            )
            .all()
        )
        ranked = _rank_procedures(procs, query)[:limit]
        out = [
            {
                "name": p.slug,
                "title": p.title,
                "description": p.description,
                "autonomy_level": _val(p.autonomy_level),
                "confidence": round(p.synthesis_confidence, 3),
                "score": round(score, 3),
            }
            for p, score in ranked
        ]
        return json.dumps({"query": query, "matches": out}, indent=2)
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def get_skill(name: str) -> str:
    """Load the full executable skill for a procedure by its `name`.

    Returns the complete SKILL.md (steps, decision rules, guardrails, sources)
    plus a structured safety block. IMPORTANT: obey `autonomy_level` before
    acting -- only 'autonomous' skills may run unattended; 'suggest_only'
    requires human approval; 'human_required' must be escalated to a person.
    """
    db = SessionLocal()
    try:
        user = _resolve_tenant(db)
        proc = (
            db.query(Procedure)
            .filter(Procedure.company_id == user.company_id, Procedure.slug == name)
            .first()
        )
        if not proc:
            return json.dumps({"error": f"No skill named '{name}'."})
        skill = compile_procedure_to_skill(proc)
        return json.dumps(
            {
                "name": skill.name,
                "skill_md": skill.markdown,
                "safety": skill.metadata,  # autonomy_level, confidence, etc.
                "warnings": skill.warnings,
            },
            indent=2,
        )
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    finally:
        db.close()


@mcp.tool()
def report_execution(name: str, success: bool, note: Optional[str] = None) -> str:
    """Report the outcome of executing a skill so the brain can learn.

    Call this AFTER you (or a human) act on a skill. Reporting successes lets a
    proven skill earn `autonomous` status over time; reporting a failure pulls
    its autonomy back down. This is how the company brain stays safe and gets
    smarter with use.
    """
    db = SessionLocal()
    try:
        user = _resolve_tenant(db)
        proc = (
            db.query(Procedure)
            .filter(Procedure.company_id == user.company_id, Procedure.slug == name)
            .first()
        )
        if not proc:
            return json.dumps({"error": f"No skill named '{name}'."})
        updated = record_execution_feedback(
            db, company_id=user.company_id, procedure_id=proc.id, success=success
        )
        return json.dumps(
            {
                "name": name,
                "recorded": True,
                "executions": updated.execution_count,
                "success_rate": round(updated.success_rate, 3)
                if updated.success_rate is not None
                else None,
                "autonomy_level": _val(updated.autonomy_level),
            }
        )
    except PermissionError as exc:
        return json.dumps({"error": str(exc)})
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Ranking (semantic when available, keyword fallback)
# --------------------------------------------------------------------------- #
def _rank_procedures(procs, query: str):
    """Return [(procedure, score)] sorted desc."""
    if _HAS_SEARCH and procs:
        try:
            qvec = create_embedding(query)
            return _semantic_rank(procs, qvec)
        except Exception:
            pass  # fall through to keyword
    return _keyword_rank(procs, query)


def _semantic_rank(procs, qvec):
    import numpy as np

    q = np.asarray(qvec, dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1.0)
    scored = []
    for p in procs:
        # Embed-free proxy: cosine on a cheap bag isn't ideal, so we reuse the
        # query embedding against a freshly embedded description only if needed.
        text = f"{p.title}. {p.description}. {p.trigger_condition or ''}"
        try:
            pv = np.asarray(create_embedding(text), dtype=np.float32)
            pv = pv / (np.linalg.norm(pv) or 1.0)
            scored.append((p, float(q @ pv)))
        except Exception:
            scored.append((p, 0.0))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


def _keyword_rank(procs, query: str):
    terms = {t for t in query.lower().split() if len(t) > 2}
    scored = []
    for p in procs:
        hay = f"{p.title} {p.description} {p.trigger_condition or ''} {p.domain or ''}".lower()
        score = sum(1 for t in terms if t in hay) / (len(terms) or 1)
        scored.append((p, score))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


def _val(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


# --------------------------------------------------------------------------- #
# HTTP transport: pull api_key from the X-API-Key header per request
# --------------------------------------------------------------------------- #
def _build_http_app():
    """Wrap the streamable-HTTP app so each request's X-API-Key populates the
    tenant context. Mount this in your FastAPI app or run standalone."""
    from starlette.middleware.base import BaseHTTPMiddleware

    app = mcp.streamable_http_app()

    class ApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            token = _api_key_ctx.set(request.headers.get("x-api-key"))
            try:
                return await call_next(request)
            finally:
                _api_key_ctx.reset(token)

    app.add_middleware(ApiKeyMiddleware)
    return app


def main():
    parser = argparse.ArgumentParser(description="KRAB Company Brain MCP server")
    parser.add_argument("--http", action="store_true", help="serve over HTTP")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8077)
    args = parser.parse_args()

    if args.http:
        import uvicorn

        uvicorn.run(_build_http_app(), host=args.host, port=args.port)
    else:
        # stdio: one tenant per process via KRAB_API_KEY.
        mcp.run()


if __name__ == "__main__":
    main()
