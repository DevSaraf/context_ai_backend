"""
KRAB — Ticket Routes (aligned with dashboard.html API calls)
Full ticket lifecycle API: CRUD, comments, AI copilot, macros, triggers, SLA, stats.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
import logging

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User, Trigger, Macro, SLAPolicy, Ticket, TicketComment
from ..schemas import (
    TicketCreate, TicketUpdate, TicketResponse, TicketListResponse,
    TicketCommentCreate, TicketCommentResponse,
    TicketStatusEnum, TicketPriorityEnum, TicketChannelEnum,
    TicketCSATRequest, TicketBulkUpdate, TicketAISuggest,
    TriggerCreate, TriggerResponse,
    MacroCreate, MacroResponse, MacroApply,
    SLAPolicyCreate, SLAPolicyResponse,
    TicketStatsResponse, TicketListParams,
)
from ..ticket_service import TicketService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])


def get_ticket_service(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return TicketService(db, user.company_id)


# ============================================================
# SERIALIZERS (avoid Pydantic from_attributes issues)
# ============================================================

def _ticket_to_dict(t: Ticket) -> dict:
    return {
        "id": t.id,
        "ticket_number": t.ticket_number,
        "subject": t.subject,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "channel": t.channel,
        "requester_email": t.requester_email,
        "requester_name": t.requester_name,
        "assigned_agent_id": t.assigned_agent_id,
        "assigned_group": t.assigned_group,
        "tags": t.tags or [],
        "category": t.category,
        "ai_intent": t.ai_intent,
        "ai_sentiment": t.ai_sentiment,
        "ai_confidence": t.ai_confidence,
        "ai_suggested_response": t.ai_suggested_response,
        "sla_breach": t.sla_breach or False,
        "first_response_due_at": t.first_response_due_at.isoformat() if t.first_response_due_at else None,
        "resolution_due_at": t.resolution_due_at.isoformat() if t.resolution_due_at else None,
        "csat_rating": t.csat_rating,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "solved_at": t.solved_at.isoformat() if t.solved_at else None,
    }


def _trigger_to_dict(t: Trigger) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "event": t.event,
        "conditions": t.conditions,
        "actions": t.actions,
        "is_active": t.is_active,
        "position": t.position or 0,
    }


def _macro_to_dict(m: Macro) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "description": m.description,
        "actions": m.actions,
        "usage_count": m.usage_count or 0,
        "is_active": m.is_active,
    }


def _sla_to_dict(p: SLAPolicy) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "is_default": p.is_default,
        "targets": p.targets,
        "business_hours": p.business_hours,
        "is_active": p.is_active,
    }


# ============================================================
# TICKET CRUD
# ============================================================

@router.post("/", status_code=201)
async def create_ticket(
    data: TicketCreate,
    user: User = Depends(get_current_user),
    svc: TicketService = Depends(get_ticket_service),
):
    ticket = svc.create_ticket(data, user_id=user.id)
    return _ticket_to_dict(ticket)


@router.get("/")
async def list_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigned_agent_id: Optional[int] = None,
    requester_email: Optional[str] = None,
    channel: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: str = "created_at",
    sort_order: str = "desc",
    user: User = Depends(get_current_user),
    svc: TicketService = Depends(get_ticket_service),
):
    params = TicketListParams(
        status=TicketStatusEnum(status) if status else None,
        priority=TicketPriorityEnum(priority) if priority else None,
        assigned_agent_id=assigned_agent_id,
        requester_email=requester_email,
        channel=TicketChannelEnum(channel) if channel else None,
        tag=tag, search=search, page=page, page_size=page_size,
        sort_by=sort_by, sort_order=sort_order,
    )
    tickets, total = svc.list_tickets(params)
    return {
        "tickets": [_ticket_to_dict(t) for t in tickets],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/stats")
async def get_ticket_stats(svc: TicketService = Depends(get_ticket_service)):
    return svc.get_stats()


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: int,
    svc: TicketService = Depends(get_ticket_service),
):
    ticket = svc.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return _ticket_to_dict(ticket)


@router.put("/{ticket_id}")
async def update_ticket(
    ticket_id: int,
    data: TicketUpdate,
    user: User = Depends(get_current_user),
    svc: TicketService = Depends(get_ticket_service),
):
    ticket = svc.update_ticket(ticket_id, data, user_id=user.id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return _ticket_to_dict(ticket)


@router.post("/bulk-update")
async def bulk_update_tickets(
    data: TicketBulkUpdate,
    user: User = Depends(get_current_user),
    svc: TicketService = Depends(get_ticket_service),
):
    updates = {}
    if data.status:
        updates["status"] = data.status.value
    if data.priority:
        updates["priority"] = data.priority.value
    if data.assigned_agent_id:
        updates["assigned_agent_id"] = data.assigned_agent_id
    if data.tags_add:
        updates["tags_add"] = data.tags_add
    if data.tags_remove:
        updates["tags_remove"] = data.tags_remove

    svc.bulk_update(data.ticket_ids, updates, user_id=user.id)
    return {"success": True, "updated": len(data.ticket_ids)}


# ============================================================
# COMMENTS — dashboard calls GET /tickets/{id}/comments
# ============================================================

@router.get("/{ticket_id}/comments")
async def get_comments(
    ticket_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all comments for a ticket."""
    ticket = db.query(Ticket).filter_by(
        id=ticket_id, company_id=user.company_id
    ).first()
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    comments = (
        db.query(TicketComment)
        .filter_by(ticket_id=ticket_id)
        .order_by(TicketComment.created_at)
        .all()
    )
    return {
        "comments": [
            {
                "id": c.id,
                "ticket_id": c.ticket_id,
                "author_type": c.author_type if hasattr(c, 'author_type') else "agent",
                "author_name": c.author_name or "Agent",
                "author_email": c.author_email if hasattr(c, 'author_email') else None,
                "body": c.body,
                "is_internal": c.is_internal,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in comments
        ]
    }


@router.post("/{ticket_id}/comments", status_code=201)
async def add_comment(
    ticket_id: int,
    data: TicketCommentCreate,
    user: User = Depends(get_current_user),
    svc: TicketService = Depends(get_ticket_service),
):
    comment = svc.add_comment(ticket_id, data, user_id=user.id)
    if not comment:
        raise HTTPException(404, "Ticket not found")
    return {
        "id": comment.id,
        "ticket_id": comment.ticket_id,
        "author_type": comment.author_type if hasattr(comment, 'author_type') else "agent",
        "author_name": comment.author_name,
        "body": comment.body,
        "is_internal": comment.is_internal,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


# ============================================================
# AI COPILOT — dashboard calls POST /tickets/{id}/copilot
# ============================================================

@router.post("/{ticket_id}/copilot")
async def get_ai_copilot(
    ticket_id: int,
    user: User = Depends(get_current_user),
    svc: TicketService = Depends(get_ticket_service),
):
    """AI copilot suggestion for a ticket (called from ticket detail modal)."""
    suggestion = svc.get_ai_suggestion(ticket_id)
    if not suggestion:
        raise HTTPException(404, "Ticket not found")
    return {
        "summary": suggestion.summary if hasattr(suggestion, 'summary') else "",
        "intent": suggestion.intent if hasattr(suggestion, 'intent') else "",
        "sentiment": suggestion.sentiment if hasattr(suggestion, 'sentiment') else "",
        "suggested_response": suggestion.suggested_response if hasattr(suggestion, 'suggested_response') else "",
        "confidence": suggestion.confidence if hasattr(suggestion, 'confidence') else 0,
        "similar_tickets": suggestion.similar_tickets if hasattr(suggestion, 'similar_tickets') else [],
        "relevant_articles": suggestion.relevant_articles if hasattr(suggestion, 'relevant_articles') else [],
        "suggested_macros": suggestion.suggested_macros if hasattr(suggestion, 'suggested_macros') else [],
    }


# Also keep GET version for flexibility
@router.get("/{ticket_id}/ai-suggest")
async def get_ai_suggestion_get(
    ticket_id: int,
    svc: TicketService = Depends(get_ticket_service),
):
    suggestion = svc.get_ai_suggestion(ticket_id)
    if not suggestion:
        raise HTTPException(404, "Ticket not found")
    return suggestion


# ============================================================
# CSAT
# ============================================================

@router.post("/{ticket_id}/csat")
async def submit_csat(
    ticket_id: int,
    data: TicketCSATRequest,
    svc: TicketService = Depends(get_ticket_service),
):
    ticket = svc.submit_csat(ticket_id, data.rating, data.comment)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return {"success": True}


# ============================================================
# TRIGGERS
# ============================================================

@router.get("/config/triggers")
async def list_triggers(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    triggers = db.query(Trigger).filter_by(company_id=user.company_id).order_by(Trigger.position).all()
    return {"triggers": [_trigger_to_dict(t) for t in triggers], "count": len(triggers)}


@router.post("/config/triggers", status_code=201)
async def create_trigger(
    data: TriggerCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trigger = Trigger(
        company_id=user.company_id,
        name=data.name,
        description=data.description,
        event=data.event,
        conditions=data.conditions,
        actions=data.actions,
    )
    db.add(trigger)
    db.commit()
    db.refresh(trigger)
    return _trigger_to_dict(trigger)


@router.put("/config/triggers/{trigger_id}")
async def update_trigger(
    trigger_id: int,
    data: TriggerCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trigger = db.query(Trigger).filter_by(id=trigger_id, company_id=user.company_id).first()
    if not trigger:
        raise HTTPException(404, "Trigger not found")

    trigger.name = data.name
    trigger.description = data.description
    trigger.event = data.event
    trigger.conditions = data.conditions
    trigger.actions = data.actions

    db.commit()
    db.refresh(trigger)
    return _trigger_to_dict(trigger)


@router.delete("/config/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trigger = db.query(Trigger).filter_by(id=trigger_id, company_id=user.company_id).first()
    if not trigger:
        raise HTTPException(404, "Trigger not found")
    db.delete(trigger)
    db.commit()
    return {"success": True}


# ============================================================
# MACROS
# ============================================================

@router.get("/config/macros")
async def list_macros(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    macros = db.query(Macro).filter_by(company_id=user.company_id, is_active=True).all()
    return {"macros": [_macro_to_dict(m) for m in macros]}


@router.post("/config/macros", status_code=201)
async def create_macro(
    data: MacroCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    macro = Macro(
        company_id=user.company_id,
        title=data.title,
        description=data.description,
        actions=data.actions,
        created_by=user.id,
    )
    db.add(macro)
    db.commit()
    db.refresh(macro)
    return _macro_to_dict(macro)


@router.post("/config/macros/apply")
async def apply_macro_to_ticket(
    data: MacroApply,
    user: User = Depends(get_current_user),
    svc: TicketService = Depends(get_ticket_service),
):
    ticket = svc.apply_macro(data.ticket_id, data.macro_id, user_id=user.id)
    if not ticket:
        raise HTTPException(404, "Ticket or macro not found")
    return {"success": True, "ticket_number": ticket.ticket_number}


@router.delete("/config/macros/{macro_id}")
async def delete_macro(
    macro_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    macro = db.query(Macro).filter_by(id=macro_id, company_id=user.company_id).first()
    if not macro:
        raise HTTPException(404, "Macro not found")
    macro.is_active = False
    db.commit()
    return {"success": True}


# ============================================================
# SLA POLICIES
# ============================================================

@router.get("/config/sla")
async def list_sla_policies(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    policies = db.query(SLAPolicy).filter_by(company_id=user.company_id, is_active=True).all()
    return {"policies": [_sla_to_dict(p) for p in policies], "count": len(policies)}


@router.post("/config/sla", status_code=201)
async def create_sla_policy(
    data: SLAPolicyCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.is_default:
        existing = db.query(SLAPolicy).filter_by(company_id=user.company_id, is_default=True).all()
        for p in existing:
            p.is_default = False

    policy = SLAPolicy(
        company_id=user.company_id,
        name=data.name,
        description=data.description,
        is_default=data.is_default,
        targets=data.targets,
        business_hours=data.business_hours,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return _sla_to_dict(policy)
