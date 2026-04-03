"""
Widget Router — Public-facing endpoints for the embeddable support widget.

These endpoints are authenticated by company API key (not JWT),
so customers can submit tickets without a KRAB account.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime

from app.database import get_db
from app import models
from app.embedding import create_embedding
from app.hybrid_search import hybrid_search
from app.rag_engine import generate_ticket_response

router = APIRouter(prefix="/widget", tags=["widget"])


def _get_company_by_api_key(db: Session, api_key: str):
    """Look up a user/company by their API key."""
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
    user = db.query(models.User).filter(models.User.api_key == api_key).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user


@router.get("/config/{api_key}")
def get_widget_config(
    api_key: str,
    db: Session = Depends(get_db)
):
    """Return widget config for this company (public endpoint)."""
    user = _get_company_by_api_key(db, api_key)

    # Get widget settings from DB or return defaults
    # For now, return defaults — can be extended with a widget_settings table later
    return {
        "company_id": user.company_id,
        "auto_respond": False,
        "brand_color": "#c6613f",
        "welcome_message": "How can we help you today?",
        "placeholder": "Describe your issue...",
    }


@router.post("/submit")
async def submit_ticket(
    data: dict,
    db: Session = Depends(get_db)
):
    """
    Public ticket submission from the embeddable widget.
    Authenticated by API key (not JWT).

    Body:
        api_key:      Company's KRAB API key
        name:         Customer name (optional)
        email:        Customer email (optional)
        subject:      Ticket subject
        message:      Customer's message
    """
    api_key = data.get("api_key")
    user = _get_company_by_api_key(db, api_key)

    subject = (data.get("subject") or "").strip()
    message = (data.get("message") or "").strip()
    customer_name = (data.get("name") or "").strip()
    customer_email = (data.get("email") or "").strip()

    if not subject and not message:
        raise HTTPException(status_code=400, detail="Provide a subject or message")

    try:
        # Search the company's knowledge base for similar content
        search_text = f"{subject}. {message}".strip()
        query_embedding = create_embedding(search_text)
        similar = hybrid_search(db, user.id, search_text, query_embedding, limit=5, filter_by="user_id")

        # Generate AI response
        rag_response = await generate_ticket_response(subject, message, similar, tone="empathetic")

        has_answer = rag_response.has_answer
        suggested = rag_response.answer if has_answer else ""
        confidence = rag_response.confidence

        # Store the ticket in the database
        db.execute(
            text("""
                INSERT INTO widget_tickets
                (user_id, company_id, customer_name, customer_email, subject, message,
                 ai_response, confidence, status, created_at)
                VALUES (:user_id, :company_id, :name, :email, :subject, :message,
                        :ai_response, :confidence, :status, :created_at)
            """),
            {
                "user_id": user.id,
                "company_id": user.company_id,
                "name": customer_name[:200] if customer_name else None,
                "email": customer_email[:200] if customer_email else None,
                "subject": subject[:500] if subject else "",
                "message": message[:5000] if message else "",
                "ai_response": suggested[:5000] if suggested else None,
                "confidence": confidence,
                "status": "auto_responded" if has_answer else "pending",
                "created_at": datetime.utcnow(),
            }
        )
        db.commit()

        # Return response based on auto_respond setting
        # For now, always return the AI response to the widget
        # The company dashboard will show these tickets for review
        return {
            "success": True,
            "has_answer": has_answer,
            "response": suggested if has_answer else "Thank you for reaching out. Our team will review your message and get back to you shortly.",
            "confidence": confidence,
            "ticket_status": "auto_responded" if has_answer else "pending",
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Widget submit error: {e}")
        import traceback
        traceback.print_exc()
        # Still acknowledge the ticket even if AI fails
        try:
            db.execute(
                text("""
                    INSERT INTO widget_tickets
                    (user_id, company_id, customer_name, customer_email, subject, message,
                     status, created_at)
                    VALUES (:user_id, :company_id, :name, :email, :subject, :message,
                            :status, :created_at)
                """),
                {
                    "user_id": user.id,
                    "company_id": user.company_id,
                    "name": customer_name[:200] if customer_name else None,
                    "email": customer_email[:200] if customer_email else None,
                    "subject": subject[:500] if subject else "",
                    "message": message[:5000] if message else "",
                    "status": "pending",
                    "created_at": datetime.utcnow(),
                }
            )
            db.commit()
        except Exception:
            pass

        return {
            "success": True,
            "has_answer": False,
            "response": "Thank you for reaching out. Our team will review your message and get back to you shortly.",
            "confidence": 0,
            "ticket_status": "pending",
        }


@router.get("/tickets")
def list_widget_tickets(
    api_key: str = None,
    db: Session = Depends(get_db)
):
    """List widget tickets for a company (used by dashboard)."""
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
    user = _get_company_by_api_key(db, api_key)

    tickets = db.execute(
        text("""
            SELECT id, customer_name, customer_email, subject, message,
                   ai_response, confidence, status, created_at
            FROM widget_tickets
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 50
        """),
        {"user_id": user.id}
    ).mappings().all()

    return {"tickets": [dict(t) for t in tickets], "total": len(tickets)}