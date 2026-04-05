"""
KRAB — Knowledge Health Service
Scores knowledge base quality: freshness, gaps, contradictions, usage.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Dict, Any, List
from datetime import datetime, timedelta
import logging

from .models import KnowledgeChunk, KnowledgeHealthReport, Ticket

logger = logging.getLogger(__name__)


class KnowledgeHealthService:
    def __init__(self, db: Session, company_id: str):
        self.db = db
        self.company_id = company_id

    def generate_report(self) -> Dict[str, Any]:
        """Generate a full knowledge health report."""
        chunks = self.db.query(KnowledgeChunk).filter_by(company_id=self.company_id).all()
        total = len(chunks)

        if total == 0:
            return {
                "overall_score": 0,
                "freshness_score": 0,
                "total_documents": 0,
                "stale_documents": 0,
                "broken_links": 0,
                "coverage_gaps": [],
                "unused_documents": 0,
                "details": {},
            }

        now = datetime.utcnow()
        six_months_ago = now - timedelta(days=180)
        three_months_ago = now - timedelta(days=90)

        # 1. Freshness score
        stale_count = 0
        aging_count = 0
        for chunk in chunks:
            last_update = chunk.last_synced_at or chunk.created_at
            if last_update and last_update < six_months_ago:
                stale_count += 1
            elif last_update and last_update < three_months_ago:
                aging_count += 1

        freshness = max(0, 100 - (stale_count / total * 100)) if total > 0 else 0

        # 2. Source diversity
        sources = set()
        for chunk in chunks:
            sources.add(chunk.source_app or "upload")

        source_score = min(100, len(sources) * 20)  # 5 sources = 100

        # 3. Coverage gaps - find frequently asked questions with low confidence
        coverage_gaps = []
        recent_tickets = (
            self.db.query(Ticket)
            .filter(
                Ticket.company_id == self.company_id,
                Ticket.created_at >= three_months_ago,
                Ticket.ai_confidence != None,
                Ticket.ai_confidence < 0.5,
            )
            .limit(20)
            .all()
        )

        for ticket in recent_tickets:
            coverage_gaps.append({
                "query": ticket.subject,
                "confidence": ticket.ai_confidence or 0,
                "ticket_number": ticket.ticket_number,
            })

        # 4. Unused documents (never appeared in search results)
        unused = sum(1 for c in chunks if (c.confidence or 0) == 0 and c.created_at and c.created_at < three_months_ago)

        # 5. Broken links
        broken_links = 0
        for chunk in chunks:
            if chunk.source_url and chunk.is_stale:
                broken_links += 1

        # 6. Content by source type breakdown
        source_breakdown = {}
        for chunk in chunks:
            src = chunk.source_app or "upload"
            source_breakdown[src] = source_breakdown.get(src, 0) + 1

        # 7. Content by type breakdown
        type_breakdown = {}
        for chunk in chunks:
            st = chunk.source_type or "document"
            type_breakdown[st] = type_breakdown.get(st, 0) + 1

        # Calculate overall score
        gap_penalty = min(30, len(coverage_gaps) * 3)
        unused_penalty = min(15, (unused / total * 15)) if total > 0 else 0
        broken_penalty = min(10, broken_links * 2)

        overall = max(0, min(100, (
            freshness * 0.35 +
            source_score * 0.25 +
            (100 - gap_penalty) * 0.25 +
            (100 - unused_penalty - broken_penalty) * 0.15
        )))

        report_data = {
            "overall_score": round(overall, 1),
            "freshness_score": round(freshness, 1),
            "total_documents": total,
            "stale_documents": stale_count,
            "aging_documents": aging_count,
            "broken_links": broken_links,
            "coverage_gaps": coverage_gaps[:10],
            "unused_documents": unused,
            "source_diversity": len(sources),
            "details": {
                "source_breakdown": source_breakdown,
                "type_breakdown": type_breakdown,
                "source_score": round(source_score, 1),
                "gap_penalty": gap_penalty,
            },
        }

        # Save report
        report = KnowledgeHealthReport(
            company_id=self.company_id,
            total_documents=total,
            stale_documents=stale_count,
            broken_links=broken_links,
            coverage_gaps=coverage_gaps[:10],
            unused_documents=unused,
            freshness_score=round(freshness, 1),
            overall_score=round(overall, 1),
            details=report_data.get("details"),
        )
        self.db.add(report)
        self.db.commit()

        report_data["generated_at"] = datetime.utcnow().isoformat()
        return report_data

    def get_latest_report(self) -> Dict[str, Any]:
        report = (
            self.db.query(KnowledgeHealthReport)
            .filter_by(company_id=self.company_id)
            .order_by(KnowledgeHealthReport.created_at.desc())
            .first()
        )
        if not report:
            return self.generate_report()

        return {
            "overall_score": report.overall_score,
            "freshness_score": report.freshness_score,
            "total_documents": report.total_documents,
            "stale_documents": report.stale_documents,
            "broken_links": report.broken_links,
            "coverage_gaps": report.coverage_gaps or [],
            "unused_documents": report.unused_documents,
            "details": report.details,
            "generated_at": report.created_at.isoformat(),
        }

    def mark_stale_documents(self, source_urls: List[str]):
        """Mark documents as stale (e.g., broken links detected)."""
        for url in source_urls:
            chunks = (
                self.db.query(KnowledgeChunk)
                .filter_by(company_id=self.company_id, source_url=url)
                .all()
            )
            for chunk in chunks:
                chunk.is_stale = True
        self.db.commit()

    def get_stale_documents(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get list of stale documents for review."""
        chunks = (
            self.db.query(KnowledgeChunk)
            .filter_by(company_id=self.company_id, is_stale=True)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": c.id,
                "title": c.source_title,
                "source_url": c.source_url,
                "source_app": c.source_app,
                "last_synced": c.last_synced_at.isoformat() if c.last_synced_at else None,
            }
            for c in chunks
        ]
