from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    company_id: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    company_id: Optional[str] = None


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


class FeedbackCreate(BaseModel):
    chunk_id: int
    feedback_type: str  # 'helpful', 'not_helpful', 'used'
    query: Optional[str] = None
    similarity_score: Optional[float] = None


class FeedbackResponse(BaseModel):
    id: int
    chunk_id: int
    feedback_type: str
    created_at: datetime


class AnalyticsResponse(BaseModel):
    total_searches: int
    total_feedback: int
    helpful_rate: float
    usage_rate: float
    top_sources: List[dict]
    recent_searches: int
    searches_by_day: List[dict]


# ============== ZENDESK SCHEMAS ==============

class ZendeskConnectRequest(BaseModel):
    subdomain: str  # e.g., "mycompany" for mycompany.zendesk.com


class ZendeskConnectResponse(BaseModel):
    oauth_url: str
    state: str


class ZendeskStatusResponse(BaseModel):
    connected: bool
    subdomain: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    tickets_imported: int = 0


class ZendeskSyncResponse(BaseModel):
    success: bool
    tickets_imported: int
    chunks_created: int
    message: str


class ZendeskTicketInfo(BaseModel):
    zendesk_ticket_id: int
    subject: str
    status: str
    csat_score: Optional[int] = None
    resolution_score: Optional[float] = None
    imported_at: datetime