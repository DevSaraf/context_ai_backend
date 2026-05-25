"""
KRAB — Help Center & Knowledge Health Routes
Aligned with dashboard.html API calls.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
import re
import logging

from ..database import get_db
from ..dependencies import get_current_user
from ..models import User, HelpArticle
from ..schemas import HelpArticleCreate, HelpArticleUpdate
from ..knowledge_health import KnowledgeHealthService

logger = logging.getLogger(__name__)

# ============================================================
# HELP CENTER (authenticated)
# ============================================================

help_router = APIRouter(prefix="/help-center", tags=["help-center"])


@help_router.get("/articles")
async def list_articles(
    status: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(HelpArticle).filter_by(company_id=user.company_id)
    if status:
        query = query.filter(HelpArticle.status == status)
    if category:
        query = query.filter(HelpArticle.category == category)
    if search:
        query = query.filter(HelpArticle.title.ilike(f"%{search}%"))

    articles = query.order_by(HelpArticle.created_at.desc()).all()

    return {
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "slug": a.slug,
                "body": a.body,
                "category": a.category,
                "section": a.section,
                "tags": a.tags or [],
                "status": a.status,
                "is_published": a.status == "published",
                "view_count": a.view_count or 0,
                "helpful_count": a.helpful_count or 0,
                "not_helpful_count": a.not_helpful_count or 0,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
            for a in articles
        ]
    }


@help_router.post("/articles", status_code=201)
async def create_article(
    data: HelpArticleCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    slug = re.sub(r'[^a-z0-9]+', '-', data.title.lower()).strip('-')

    # Dashboard sends is_published as part of the body
    status = data.status if data.status else "draft"

    article = HelpArticle(
        company_id=user.company_id,
        title=data.title,
        slug=slug,
        body=data.body,
        body_html=data.body,
        category=data.category,
        section=data.section,
        tags=data.tags,
        status=status,
        author_id=user.id,
        published_at=datetime.utcnow() if status == "published" else None,
    )
    db.add(article)
    db.commit()
    db.refresh(article)

    return {
        "id": article.id,
        "title": article.title,
        "slug": article.slug,
        "status": article.status,
        "created_at": article.created_at.isoformat() if article.created_at else None,
    }


@help_router.get("/articles/{article_id}")
async def get_article(
    article_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    article = db.query(HelpArticle).filter_by(id=article_id, company_id=user.company_id).first()
    if not article:
        raise HTTPException(404, "Article not found")
    return {
        "id": article.id,
        "title": article.title,
        "slug": article.slug,
        "body": article.body,
        "category": article.category,
        "section": article.section,
        "tags": article.tags or [],
        "status": article.status,
        "is_published": article.status == "published",
        "view_count": article.view_count or 0,
        "helpful_count": article.helpful_count or 0,
        "not_helpful_count": article.not_helpful_count or 0,
        "created_at": article.created_at.isoformat() if article.created_at else None,
        "updated_at": article.updated_at.isoformat() if article.updated_at else None,
    }


@help_router.put("/articles/{article_id}")
async def update_article(
    article_id: int,
    data: dict,  # Accept raw dict since dashboard may send is_published
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    article = db.query(HelpArticle).filter_by(id=article_id, company_id=user.company_id).first()
    if not article:
        raise HTTPException(404, "Article not found")

    if "title" in data and data["title"]:
        article.title = data["title"]
        article.slug = re.sub(r'[^a-z0-9]+', '-', data["title"].lower()).strip('-')
    if "body" in data and data["body"]:
        article.body = data["body"]
        article.body_html = data["body"]
    if "category" in data:
        article.category = data["category"]
    if "section" in data:
        article.section = data["section"]
    if "tags" in data:
        article.tags = data["tags"]
    if "status" in data:
        article.status = data["status"]
        if data["status"] == "published" and not article.published_at:
            article.published_at = datetime.utcnow()
    if "is_published" in data:
        article.status = "published" if data["is_published"] else "draft"
        if data["is_published"] and not article.published_at:
            article.published_at = datetime.utcnow()

    db.commit()
    db.refresh(article)

    return {
        "id": article.id,
        "title": article.title,
        "status": article.status,
        "updated_at": article.updated_at.isoformat() if article.updated_at else None,
    }


@help_router.delete("/articles/{article_id}")
async def delete_article(
    article_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    article = db.query(HelpArticle).filter_by(id=article_id, company_id=user.company_id).first()
    if not article:
        raise HTTPException(404, "Article not found")
    db.delete(article)
    db.commit()
    return {"success": True}


@help_router.post("/articles/{article_id}/feedback")
async def article_feedback(
    article_id: int,
    helpful: bool = True,
    db: Session = Depends(get_db),
):
    """Public endpoint — no auth required. Track article helpfulness."""
    article = db.query(HelpArticle).filter_by(id=article_id).first()
    if not article:
        raise HTTPException(404, "Article not found")

    if helpful:
        article.helpful_count = (article.helpful_count or 0) + 1
    else:
        article.not_helpful_count = (article.not_helpful_count or 0) + 1

    db.commit()
    return {"success": True}


# ============================================================
# PUBLIC HELP CENTER (no auth)
# ============================================================

public_help_router = APIRouter(prefix="/public/help", tags=["public-help"])


@public_help_router.get("/articles")
async def public_list_articles(
    company_id: str = Query(...),
    category: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(HelpArticle).filter_by(company_id=company_id, status="published")
    if category:
        query = query.filter(HelpArticle.category == category)
    if search:
        query = query.filter(HelpArticle.title.ilike(f"%{search}%"))

    articles = query.order_by(HelpArticle.is_promoted.desc(), HelpArticle.published_at.desc()).all()
    return {
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "slug": a.slug,
                "category": a.category,
                "tags": a.tags or [],
                "published_at": a.published_at.isoformat() if a.published_at else None,
            }
            for a in articles
        ]
    }


@public_help_router.get("/articles/{slug}")
async def public_get_article(
    slug: str,
    company_id: str = Query(...),
    db: Session = Depends(get_db),
):
    article = db.query(HelpArticle).filter_by(slug=slug, company_id=company_id, status="published").first()
    if not article:
        raise HTTPException(404, "Article not found")

    article.view_count = (article.view_count or 0) + 1
    db.commit()

    return {
        "id": article.id,
        "title": article.title,
        "body": article.body,
        "category": article.category,
        "tags": article.tags or [],
    }


@public_help_router.get("/categories")
async def public_list_categories(
    company_id: str = Query(...),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func
    categories = (
        db.query(HelpArticle.category, func.count())
        .filter_by(company_id=company_id, status="published")
        .filter(HelpArticle.category != None)
        .group_by(HelpArticle.category)
        .all()
    )
    return {"categories": [{"name": c[0], "count": c[1]} for c in categories]}


# ============================================================
# KNOWLEDGE HEALTH
# ============================================================

health_router = APIRouter(prefix="/knowledge-health", tags=["knowledge-health"])


@health_router.get("/")
async def get_health_report(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = KnowledgeHealthService(db, user.company_id)
    report = service.get_latest_report()

    return {
        "overall_score": report.get("overall_score", 0),
        "freshness_score": report.get("freshness_score", 0),
        "total_documents": report.get("total_documents", 0),
        "stale_count": report.get("stale_documents", 0),
        "fresh_count": report.get("total_documents", 0) - report.get("stale_documents", 0),
        "gap_count": len(report.get("coverage_gaps", [])),
        "unused_documents": report.get("unused_documents", 0),
        "broken_links": report.get("broken_links", 0),
        "last_check": report.get("generated_at"),
        "details": report.get("details"),
        "generated_at": report.get("generated_at", datetime.utcnow().isoformat()),
    }


@health_router.post("/generate")
async def generate_health_report(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = KnowledgeHealthService(db, user.company_id)
    report = service.generate_report()

    return {
        "overall_score": report.get("overall_score", 0),
        "freshness_score": report.get("freshness_score", 0),
        "total_documents": report.get("total_documents", 0),
        "stale_count": report.get("stale_documents", 0),
        "fresh_count": report.get("total_documents", 0) - report.get("stale_documents", 0),
        "gap_count": len(report.get("coverage_gaps", [])),
        "last_check": report.get("generated_at"),
        "generated_at": report.get("generated_at", datetime.utcnow().isoformat()),
    }
