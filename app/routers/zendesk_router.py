"""Zendesk integration routes: connect, callback, sync, status, disconnect, tickets."""

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import secrets

from app.database import get_db
from app import models
from app.dependencies import get_current_user
from app.embedding import create_embedding
from app.models import KnowledgeChunk, ZendeskIntegration, ZendeskTicket
from app.integrations.zendesk import (
    ZendeskClient, get_oauth_url, exchange_code_for_token,
    calculate_resolution_score, format_ticket_for_embedding
)

router = APIRouter(prefix="/integrations/zendesk", tags=["zendesk"])


@router.post("/connect")
def zendesk_connect(
    request: dict,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Start Zendesk OAuth flow — returns URL to redirect user to."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    subdomain = request.get("subdomain", "")
    if not subdomain:
        raise HTTPException(status_code=400, detail="Subdomain required")

    state = f"{user.company_id}:{secrets.token_urlsafe(16)}"
    oauth_url = get_oauth_url(subdomain, state)

    existing = db.query(ZendeskIntegration).filter(ZendeskIntegration.company_id == user.company_id).first()
    if existing:
        existing.subdomain = subdomain
    else:
        integration = ZendeskIntegration(
            company_id=user.company_id,
            subdomain=subdomain,
            access_token=""
        )
        db.add(integration)
    db.commit()

    return {"oauth_url": oauth_url, "state": state}


@router.get("/callback")
async def zendesk_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db)
):
    """Handle Zendesk OAuth callback."""
    try:
        company_id = state.split(":")[0]
    except Exception:
        return {"error": "Invalid state parameter"}

    integration = db.query(ZendeskIntegration).filter(ZendeskIntegration.company_id == company_id).first()
    if not integration:
        return {"error": "Integration not found"}

    try:
        token_data = await exchange_code_for_token(integration.subdomain, code)

        integration.access_token = token_data.get("access_token")
        integration.refresh_token = token_data.get("refresh_token")

        expires_in = token_data.get("expires_in")
        if expires_in:
            integration.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        db.commit()
        return RedirectResponse(url="https://krabai.tech/dashboard.html?zendesk=connected")

    except Exception as e:
        print(f"Zendesk OAuth error: {e}")
        return RedirectResponse(url="https://krabai.tech/dashboard.html?zendesk=error")


@router.get("/status")
def zendesk_status(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Check Zendesk connection status."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"connected": False}

    integration = db.query(ZendeskIntegration).filter(ZendeskIntegration.company_id == user.company_id).first()

    if not integration or not integration.access_token:
        return {"connected": False}

    return {
        "connected": True,
        "subdomain": integration.subdomain,
        "last_sync_at": integration.last_sync_at,
        "tickets_imported": integration.tickets_imported or 0
    }


@router.post("/sync")
async def zendesk_sync(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Sync tickets from Zendesk."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"success": False, "message": "User not found"}

    integration = db.query(ZendeskIntegration).filter(ZendeskIntegration.company_id == user.company_id).first()
    if not integration or not integration.access_token:
        return {"success": False, "message": "Zendesk not connected"}

    try:
        client = ZendeskClient(integration.subdomain, integration.access_token)

        if not await client.verify_connection():
            return {"success": False, "message": "Zendesk connection invalid. Please reconnect."}

        # Fetch CSAT ratings
        csat_map = {}
        try:
            ratings_data = await client.get_satisfaction_ratings()
            for rating in ratings_data.get("satisfaction_ratings", []):
                ticket_id = rating.get("ticket_id")
                score = rating.get("score")
                if ticket_id and score:
                    csat_map[ticket_id] = 5 if score == "good" else 1
        except Exception as e:
            print(f"Could not fetch CSAT ratings: {e}")

        tickets_imported = 0
        chunks_created = 0

        for page in range(1, 6):
            try:
                tickets_data = await client.get_tickets(page=page)
                tickets = tickets_data.get("tickets", [])

                if not tickets:
                    break

                for ticket in tickets:
                    ticket_id = ticket.get("id")

                    existing = db.query(ZendeskTicket).filter(
                        ZendeskTicket.company_id == user.company_id,
                        ZendeskTicket.zendesk_ticket_id == ticket_id
                    ).first()

                    if existing:
                        continue

                    if ticket.get("status") not in ["solved", "closed"]:
                        continue

                    try:
                        comments_data = await client.get_ticket_comments(ticket_id)
                        comments = comments_data.get("comments", [])
                    except Exception:
                        comments = []

                    ticket_text = format_ticket_for_embedding(ticket, comments)

                    if len(ticket_text) < 50:
                        continue

                    csat_score = csat_map.get(ticket_id)
                    resolution_score = calculate_resolution_score(csat_score)

                    embedding = create_embedding(ticket_text)

                    chunk = KnowledgeChunk(
                        company_id=user.company_id,
                        source_type="zendesk_ticket",
                        source_id=ticket_id,
                        text=ticket_text,
                        embedding=embedding,
                        resolution_score=resolution_score
                    )
                    db.add(chunk)
                    db.flush()

                    zd_ticket = ZendeskTicket(
                        company_id=user.company_id,
                        zendesk_ticket_id=ticket_id,
                        subject=ticket.get("subject", "")[:255],
                        status=ticket.get("status"),
                        priority=ticket.get("priority"),
                        csat_score=csat_score,
                        resolution_score=resolution_score,
                        chunk_id=chunk.id,
                        ticket_created_at=ticket.get("created_at"),
                        ticket_updated_at=ticket.get("updated_at")
                    )
                    db.add(zd_ticket)

                    tickets_imported += 1
                    chunks_created += 1

            except Exception as e:
                print(f"Error fetching page {page}: {e}")
                break

        integration.last_sync_at = datetime.utcnow()
        integration.tickets_imported = (integration.tickets_imported or 0) + tickets_imported
        db.commit()

        return {
            "success": True,
            "tickets_imported": tickets_imported,
            "chunks_created": chunks_created,
            "message": f"Successfully imported {tickets_imported} tickets"
        }

    except Exception as e:
        print(f"Zendesk sync error: {e}")
        return {"success": False, "message": str(e)}


@router.delete("/disconnect")
def zendesk_disconnect(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Disconnect Zendesk integration."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"success": False, "message": "User not found"}

    integration = db.query(ZendeskIntegration).filter(ZendeskIntegration.company_id == user.company_id).first()
    if integration:
        db.delete(integration)
        db.commit()

    return {"success": True, "message": "Zendesk disconnected"}


@router.get("/tickets")
def get_zendesk_tickets(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user)
):
    """Get list of imported Zendesk tickets."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return {"tickets": []}

    tickets = db.query(ZendeskTicket).filter(
        ZendeskTicket.company_id == user.company_id
    ).order_by(ZendeskTicket.imported_at.desc()).limit(100).all()

    return {
        "tickets": [
            {
                "zendesk_ticket_id": t.zendesk_ticket_id,
                "subject": t.subject,
                "status": t.status,
                "csat_score": t.csat_score,
                "resolution_score": t.resolution_score,
                "imported_at": t.imported_at
            }
            for t in tickets
        ]
    }
