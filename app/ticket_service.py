"""
KRAB — Ticket Service
Full ticket lifecycle management with AI copilot, triggers, macros, SLA.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc, asc
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import logging
import json
import re

from .models import (
    Ticket, TicketComment, TicketEvent, TicketCounter,
    Trigger, Macro, SLAPolicy, KnowledgeChunk, HelpArticle,
    TicketStatus, TicketPriority,
)
from .schemas import (
    TicketCreate, TicketUpdate, TicketCommentCreate,
    TicketListParams, TicketAISuggest, MacroApply,
)

logger = logging.getLogger(__name__)


class TicketService:
    def __init__(self, db: Session, company_id: str, llm_provider=None, rag_engine=None):
        self.db = db
        self.company_id = company_id
        self.llm = llm_provider
        self.rag = rag_engine

    # ============================================================
    # TICKET CRUD
    # ============================================================

    def create_ticket(self, data: TicketCreate, user_id: int = None) -> Ticket:
        """Create a new ticket with auto-incrementing ticket number."""

        # Generate ticket number
        counter = self.db.query(TicketCounter).filter_by(company_id=self.company_id).first()
        if not counter:
            counter = TicketCounter(company_id=self.company_id, last_number=0)
            self.db.add(counter)

        counter.last_number += 1
        ticket_number = f"KRAB-{counter.last_number:04d}"

        ticket = Ticket(
            company_id=self.company_id,
            ticket_number=ticket_number,
            subject=data.subject,
            description=data.description,
            status=TicketStatus.NEW,
            priority=data.priority.value if data.priority else "normal",
            channel=data.channel.value if data.channel else "manual",
            requester_email=data.requester_email,
            requester_name=data.requester_name,
            assigned_agent_id=data.assigned_agent_id,
            tags=data.tags or [],
            category=data.category,
        )
        self.db.add(ticket)
        self.db.flush()

        # Create initial comment from description
        if data.description:
            comment = TicketComment(
                ticket_id=ticket.id,
                author_type="customer",
                author_name=data.requester_name or data.requester_email or "Customer",
                author_email=data.requester_email,
                body=data.description,
                is_internal=False,
            )
            self.db.add(comment)

        # Log creation event
        self._log_event(ticket.id, "created", actor_id=user_id, actor_type="system")

        # Apply SLA policy
        self._apply_sla(ticket)

        # Run triggers
        self._run_triggers(ticket, "ticket_created")

        # AI analysis
        self._ai_analyze_ticket(ticket)

        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def get_ticket(self, ticket_id: int) -> Optional[Ticket]:
        return self.db.query(Ticket).filter_by(id=ticket_id, company_id=self.company_id).first()

    def get_ticket_by_number(self, ticket_number: str) -> Optional[Ticket]:
        return self.db.query(Ticket).filter_by(ticket_number=ticket_number, company_id=self.company_id).first()

    def list_tickets(self, params: TicketListParams) -> Tuple[List[Ticket], int]:
        """List tickets with filters, sorting, and pagination."""
        query = self.db.query(Ticket).filter_by(company_id=self.company_id)

        # Apply filters
        if params.status:
            query = query.filter(Ticket.status == params.status.value)
        if params.priority:
            query = query.filter(Ticket.priority == params.priority.value)
        if params.assigned_agent_id:
            query = query.filter(Ticket.assigned_agent_id == params.assigned_agent_id)
        if params.requester_email:
            query = query.filter(Ticket.requester_email == params.requester_email)
        if params.channel:
            query = query.filter(Ticket.channel == params.channel.value)
        if params.tag:
            query = query.filter(Ticket.tags.contains([params.tag]))
        if params.search:
            search_term = f"%{params.search}%"
            query = query.filter(
                or_(
                    Ticket.subject.ilike(search_term),
                    Ticket.description.ilike(search_term),
                    Ticket.ticket_number.ilike(search_term),
                )
            )

        # Count total
        total = query.count()

        # Sort
        sort_col = getattr(Ticket, params.sort_by, Ticket.created_at)
        if params.sort_order == "asc":
            query = query.order_by(asc(sort_col))
        else:
            query = query.order_by(desc(sort_col))

        # Paginate
        offset = (params.page - 1) * params.page_size
        tickets = query.offset(offset).limit(params.page_size).all()

        return tickets, total

    def update_ticket(self, ticket_id: int, data: TicketUpdate, user_id: int = None) -> Optional[Ticket]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None

        # Track changes
        changes = []

        if data.status is not None and data.status.value != ticket.status:
            old = ticket.status
            ticket.status = data.status.value
            changes.append(("status", old, data.status.value))

            # Set timestamps
            if data.status.value == "solved":
                ticket.solved_at = datetime.utcnow()
            elif data.status.value == "closed":
                ticket.closed_at = datetime.utcnow()

        if data.priority is not None and data.priority.value != ticket.priority:
            old = ticket.priority
            ticket.priority = data.priority.value
            changes.append(("priority", old, data.priority.value))

        if data.assigned_agent_id is not None and data.assigned_agent_id != ticket.assigned_agent_id:
            old = str(ticket.assigned_agent_id) if ticket.assigned_agent_id else None
            ticket.assigned_agent_id = data.assigned_agent_id
            changes.append(("assigned_agent_id", old, str(data.assigned_agent_id)))

            # Open ticket when assigned
            if ticket.status == "new":
                ticket.status = "open"
                changes.append(("status", "new", "open"))

        if data.assigned_group is not None:
            ticket.assigned_group = data.assigned_group

        if data.tags is not None:
            old_tags = ticket.tags or []
            ticket.tags = data.tags
            if set(old_tags) != set(data.tags):
                changes.append(("tags", json.dumps(old_tags), json.dumps(data.tags)))

        if data.category is not None:
            ticket.category = data.category

        # Log events
        for field, old_val, new_val in changes:
            self._log_event(
                ticket.id,
                f"{field}_changed",
                field_name=field,
                old_value=old_val,
                new_value=new_val,
                actor_id=user_id,
                actor_type="user" if user_id else "system",
            )

        # Run triggers
        if changes:
            self._run_triggers(ticket, "ticket_updated")

        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def add_comment(self, ticket_id: int, data: TicketCommentCreate, user_id: int = None) -> Optional[TicketComment]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None

        comment = TicketComment(
            ticket_id=ticket_id,
            author_id=user_id,
            author_type=data.author_type,
            author_name=data.author_name,
            author_email=data.author_email,
            body=data.body,
            is_internal=data.is_internal,
        )
        self.db.add(comment)

        # Track first response
        if not data.is_internal and data.author_type == "agent" and not ticket.first_response_at:
            ticket.first_response_at = datetime.utcnow()

        # If agent replies publicly, set to open
        if not data.is_internal and data.author_type == "agent" and ticket.status == "new":
            ticket.status = "open"

        # If customer replies, reopen pending tickets
        if data.author_type == "customer" and ticket.status == "pending":
            ticket.status = "open"

        self._log_event(ticket_id, "commented", actor_id=user_id, actor_type=data.author_type)

        self.db.commit()
        self.db.refresh(comment)
        return comment

    def bulk_update(self, ticket_ids: List[int], updates: Dict[str, Any], user_id: int = None):
        """Bulk update multiple tickets at once."""
        for tid in ticket_ids:
            ticket = self.get_ticket(tid)
            if not ticket:
                continue

            if "status" in updates:
                ticket.status = updates["status"]
            if "priority" in updates:
                ticket.priority = updates["priority"]
            if "assigned_agent_id" in updates:
                ticket.assigned_agent_id = updates["assigned_agent_id"]
            if "tags_add" in updates:
                current = ticket.tags or []
                ticket.tags = list(set(current + updates["tags_add"]))
            if "tags_remove" in updates:
                current = ticket.tags or []
                ticket.tags = [t for t in current if t not in updates["tags_remove"]]

        self.db.commit()

    def submit_csat(self, ticket_id: int, rating: int, comment: str = None):
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None
        ticket.csat_rating = rating
        ticket.csat_comment = comment
        self.db.commit()
        return ticket

    # ============================================================
    # AI COPILOT
    # ============================================================

    def _ai_analyze_ticket(self, ticket: Ticket):
        """Run AI analysis on a new ticket: intent, sentiment, suggested response."""
        if not self.llm:
            return

        try:
            text = f"Subject: {ticket.subject}\n\nMessage: {ticket.description or ''}"

            # Intent + sentiment classification
            # This would call self.llm.generate() in production
            # For now, set basic defaults
            ticket.ai_intent = "general"
            ticket.ai_sentiment = "neutral"
            ticket.ai_confidence = 0.5

        except Exception as e:
            logger.warning(f"AI analysis failed: {e}")

    def get_ai_suggestion(self, ticket_id: int) -> Optional[TicketAISuggest]:
        """Get AI copilot suggestions for a ticket."""
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None

        text = f"Subject: {ticket.subject}\nMessage: {ticket.description or ''}"

        # Get similar chunks from KB
        similar_chunks = []
        if self.rag:
            try:
                results = self.rag.search(text, top_k=5, company_id=self.company_id)
                similar_chunks = results
            except Exception:
                pass

        # Get similar past tickets
        similar_tickets = self._find_similar_tickets(ticket)

        # Get relevant help articles
        relevant_articles = self._find_relevant_articles(text)

        # Get suggested macros
        suggested_macros = self._suggest_macros(ticket)

        # Generate AI response
        suggested_response = ""
        confidence = 0.5
        if self.rag:
            try:
                answer = self.rag.ask(text, company_id=self.company_id)
                suggested_response = answer.get("answer", "")
                confidence = answer.get("confidence", 0.5)
            except Exception:
                pass

        # Build summary
        comments = self.db.query(TicketComment).filter_by(ticket_id=ticket_id).order_by(TicketComment.created_at).all()
        summary = f"Ticket about: {ticket.subject}."
        if len(comments) > 1:
            summary += f" {len(comments)} messages exchanged."
        if ticket.ai_sentiment:
            summary += f" Customer sentiment: {ticket.ai_sentiment}."

        return TicketAISuggest(
            summary=summary,
            intent=ticket.ai_intent or "unknown",
            sentiment=ticket.ai_sentiment or "neutral",
            suggested_response=suggested_response,
            confidence=confidence,
            similar_tickets=[
                {"id": t.id, "number": t.ticket_number, "subject": t.subject, "status": t.status}
                for t in similar_tickets[:3]
            ],
            relevant_articles=[
                {"id": a.id, "title": a.title, "category": a.category}
                for a in relevant_articles[:3]
            ],
            suggested_macros=[
                {"id": m.id, "title": m.title}
                for m in suggested_macros[:3]
            ],
        )

    def _find_similar_tickets(self, ticket: Ticket) -> List[Ticket]:
        """Find similar previously resolved tickets."""
        words = set(re.findall(r'\w+', (ticket.subject + " " + (ticket.description or "")).lower()))
        common_words = {"the", "a", "an", "is", "are", "was", "were", "i", "my", "me", "we", "you", "it", "to", "and", "or", "not", "can", "do", "have", "has", "been", "this", "that", "with", "for", "on", "in", "at", "of"}
        keywords = words - common_words

        if not keywords:
            return []

        query = self.db.query(Ticket).filter(
            Ticket.company_id == self.company_id,
            Ticket.id != ticket.id,
            Ticket.status.in_(["solved", "closed"]),
        )

        # Filter by subject keywords
        conditions = [Ticket.subject.ilike(f"%{kw}%") for kw in list(keywords)[:5]]
        if conditions:
            query = query.filter(or_(*conditions))

        return query.order_by(desc(Ticket.solved_at)).limit(5).all()

    def _find_relevant_articles(self, text: str) -> List[HelpArticle]:
        words = set(re.findall(r'\w+', text.lower()))
        common_words = {"the", "a", "an", "is", "are", "i", "my", "me", "to", "and", "or", "not"}
        keywords = list(words - common_words)[:5]

        if not keywords:
            return []

        query = self.db.query(HelpArticle).filter(
            HelpArticle.company_id == self.company_id,
            HelpArticle.status == "published",
        )
        conditions = [HelpArticle.title.ilike(f"%{kw}%") for kw in keywords]
        query = query.filter(or_(*conditions))

        return query.limit(5).all()

    def _suggest_macros(self, ticket: Ticket) -> List[Macro]:
        """Suggest relevant macros based on ticket content."""
        macros = self.db.query(Macro).filter_by(company_id=self.company_id, is_active=True).all()

        # Score macros by relevance (simple keyword matching)
        text = (ticket.subject + " " + (ticket.description or "")).lower()
        scored = []
        for macro in macros:
            title_words = set(macro.title.lower().split())
            overlap = len(title_words & set(text.split()))
            scored.append((overlap, macro))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for score, m in scored if score > 0][:5]

    # ============================================================
    # TRIGGERS ENGINE
    # ============================================================

    def _run_triggers(self, ticket: Ticket, event: str):
        """Evaluate and run all active triggers for the given event."""
        triggers = (
            self.db.query(Trigger)
            .filter_by(company_id=self.company_id, event=event, is_active=True)
            .order_by(Trigger.position)
            .all()
        )

        for trigger in triggers:
            if self._evaluate_conditions(ticket, trigger.conditions):
                self._execute_actions(ticket, trigger.actions)
                logger.info(f"Trigger '{trigger.name}' fired for ticket {ticket.ticket_number}")

    def _evaluate_conditions(self, ticket: Ticket, conditions: Dict) -> bool:
        """Evaluate trigger conditions against a ticket."""
        all_conditions = conditions.get("all", [])
        any_conditions = conditions.get("any", [])

        # All conditions must match
        if all_conditions:
            for cond in all_conditions:
                if not self._check_condition(ticket, cond):
                    return False

        # At least one "any" condition must match
        if any_conditions:
            if not any(self._check_condition(ticket, c) for c in any_conditions):
                return False

        return True

    def _check_condition(self, ticket: Ticket, condition: Dict) -> bool:
        field = condition.get("field", "")
        operator = condition.get("operator", "is")
        value = condition.get("value", "")

        ticket_value = ""
        if field == "status":
            ticket_value = ticket.status or ""
        elif field == "priority":
            ticket_value = ticket.priority or ""
        elif field == "channel":
            ticket_value = ticket.channel or ""
        elif field == "subject":
            ticket_value = ticket.subject or ""
        elif field == "description":
            ticket_value = ticket.description or ""
        elif field == "requester_email":
            ticket_value = ticket.requester_email or ""
        elif field == "tags":
            ticket_value = json.dumps(ticket.tags or [])

        if operator == "is":
            return ticket_value.lower() == str(value).lower()
        elif operator == "is_not":
            return ticket_value.lower() != str(value).lower()
        elif operator == "contains":
            if isinstance(value, list):
                return any(v.lower() in ticket_value.lower() for v in value)
            return str(value).lower() in ticket_value.lower()
        elif operator == "not_contains":
            return str(value).lower() not in ticket_value.lower()
        elif operator == "starts_with":
            return ticket_value.lower().startswith(str(value).lower())

        return False

    def _execute_actions(self, ticket: Ticket, actions: List[Dict]):
        """Execute trigger actions on a ticket."""
        for action in actions:
            action_type = action.get("type", "")
            value = action.get("value", "")

            if action_type == "set_status":
                ticket.status = value
            elif action_type == "set_priority":
                ticket.priority = value
            elif action_type == "add_tag":
                tags = ticket.tags or []
                if value not in tags:
                    tags.append(value)
                    ticket.tags = tags
            elif action_type == "remove_tag":
                tags = ticket.tags or []
                ticket.tags = [t for t in tags if t != value]
            elif action_type == "assign_to_agent":
                ticket.assigned_agent_id = int(value) if value else None
            elif action_type == "assign_to_group":
                ticket.assigned_group = value
            elif action_type == "set_category":
                ticket.category = value
            elif action_type == "add_comment":
                # Template variables
                body = value
                body = body.replace("{{requester.name}}", ticket.requester_name or "Customer")
                body = body.replace("{{requester.email}}", ticket.requester_email or "")
                body = body.replace("{{ticket.number}}", ticket.ticket_number)
                body = body.replace("{{ticket.subject}}", ticket.subject)

                comment = TicketComment(
                    ticket_id=ticket.id,
                    author_type="system",
                    author_name="Automation",
                    body=body,
                    is_internal=False,
                )
                self.db.add(comment)

    # ============================================================
    # MACROS
    # ============================================================

    def apply_macro(self, ticket_id: int, macro_id: int, user_id: int = None) -> Optional[Ticket]:
        ticket = self.get_ticket(ticket_id)
        macro = self.db.query(Macro).filter_by(id=macro_id, company_id=self.company_id).first()
        if not ticket or not macro:
            return None

        self._execute_actions(ticket, macro.actions)

        # Update usage
        macro.usage_count = (macro.usage_count or 0) + 1
        macro.last_used_at = datetime.utcnow()

        self._log_event(ticket.id, "macro_applied", new_value=macro.title, actor_id=user_id, actor_type="user")

        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    # ============================================================
    # SLA
    # ============================================================

    def _apply_sla(self, ticket: Ticket):
        """Apply SLA policy to a ticket based on priority."""
        policy = (
            self.db.query(SLAPolicy)
            .filter_by(company_id=self.company_id, is_active=True, is_default=True)
            .first()
        )
        if not policy:
            return

        ticket.sla_policy_id = policy.id
        targets = policy.targets or {}
        priority_targets = targets.get(ticket.priority, targets.get("normal", {}))

        if priority_targets:
            now = datetime.utcnow()
            first_response_mins = priority_targets.get("first_response")
            resolution_mins = priority_targets.get("resolution")

            if first_response_mins:
                ticket.first_response_due_at = now + timedelta(minutes=first_response_mins)
            if resolution_mins:
                ticket.resolution_due_at = now + timedelta(minutes=resolution_mins)

    def check_sla_breaches(self):
        """Check all open tickets for SLA breaches. Run periodically."""
        now = datetime.utcnow()
        breached = self.db.query(Ticket).filter(
            Ticket.company_id == self.company_id,
            Ticket.status.in_(["new", "open", "pending"]),
            Ticket.sla_breach == False,
            or_(
                and_(Ticket.first_response_due_at != None, Ticket.first_response_at == None, Ticket.first_response_due_at < now),
                and_(Ticket.resolution_due_at != None, Ticket.solved_at == None, Ticket.resolution_due_at < now),
            )
        ).all()

        for ticket in breached:
            ticket.sla_breach = True
            self._log_event(ticket.id, "sla_breach", actor_type="system")

        self.db.commit()
        return len(breached)

    # ============================================================
    # STATS
    # ============================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get ticket statistics for the dashboard."""
        base = self.db.query(Ticket).filter_by(company_id=self.company_id)

        total = base.count()
        open_count = base.filter(Ticket.status == "open").count()
        pending = base.filter(Ticket.status == "pending").count()
        solved = base.filter(Ticket.status.in_(["solved", "closed"])).count()

        # Average first response time (in minutes)
        avg_fr = None
        responded = base.filter(Ticket.first_response_at != None).all()
        if responded:
            deltas = [(t.first_response_at - t.created_at).total_seconds() / 60 for t in responded if t.first_response_at and t.created_at]
            if deltas:
                avg_fr = sum(deltas) / len(deltas)

        # Average resolution time
        avg_res = None
        resolved = base.filter(Ticket.solved_at != None).all()
        if resolved:
            deltas = [(t.solved_at - t.created_at).total_seconds() / 60 for t in resolved if t.solved_at and t.created_at]
            if deltas:
                avg_res = sum(deltas) / len(deltas)

        # CSAT
        rated = base.filter(Ticket.csat_rating != None).all()
        csat_avg = sum(t.csat_rating for t in rated) / len(rated) if rated else None

        # SLA compliance
        sla_tickets = base.filter(Ticket.sla_policy_id != None).count()
        sla_breached = base.filter(Ticket.sla_breach == True).count()
        sla_rate = ((sla_tickets - sla_breached) / sla_tickets * 100) if sla_tickets > 0 else None

        # By channel
        channels = {}
        for row in self.db.query(Ticket.channel, func.count()).filter_by(company_id=self.company_id).group_by(Ticket.channel).all():
            channels[row[0] or "unknown"] = row[1]

        # By priority
        priorities = {}
        for row in self.db.query(Ticket.priority, func.count()).filter_by(company_id=self.company_id).group_by(Ticket.priority).all():
            priorities[row[0] or "normal"] = row[1]

        # By category
        categories = {}
        for row in self.db.query(Ticket.category, func.count()).filter_by(company_id=self.company_id).filter(Ticket.category != None).group_by(Ticket.category).all():
            categories[row[0]] = row[1]

        # Daily volume (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        daily = []
        for row in (
            self.db.query(func.date(Ticket.created_at), func.count())
            .filter(Ticket.company_id == self.company_id, Ticket.created_at >= thirty_days_ago)
            .group_by(func.date(Ticket.created_at))
            .order_by(func.date(Ticket.created_at))
            .all()
        ):
            daily.append({"date": str(row[0]), "count": row[1]})

        return {
            "total_tickets": total,
            "open_tickets": open_count,
            "pending_tickets": pending,
            "solved_tickets": solved,
            "avg_first_response_minutes": round(avg_fr, 1) if avg_fr else None,
            "avg_resolution_minutes": round(avg_res, 1) if avg_res else None,
            "sla_compliance_rate": round(sla_rate, 1) if sla_rate is not None else None,
            "csat_average": round(csat_avg, 2) if csat_avg else None,
            "tickets_by_channel": channels,
            "tickets_by_priority": priorities,
            "tickets_by_category": categories,
            "daily_volume": daily,
        }

    # ============================================================
    # HELPERS
    # ============================================================

    def _log_event(self, ticket_id: int, event_type: str, field_name: str = None,
                   old_value: str = None, new_value: str = None,
                   actor_id: int = None, actor_type: str = "system"):
        event = TicketEvent(
            ticket_id=ticket_id,
            event_type=event_type,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            actor_id=actor_id,
            actor_type=actor_type,
        )
        self.db.add(event)
