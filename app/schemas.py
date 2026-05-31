"""
KRAB — Pydantic Schemas for API requests/responses
"""

from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ============================================================
# AUTH (existing, kept)
# ============================================================

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    company_id: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    company_id: str = "default"


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    email: str
    company_id: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    company_id: Optional[str] = None


# Legacy alias
ChangePassword = ChangePasswordRequest


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


# ============================================================
# KNOWLEDGE (enhanced)
# ============================================================

class UploadTextRequest(BaseModel):
    content: str
    source_type: str = "document"


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class AskRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None  # for follow-up questions


class AskResponse(BaseModel):
    answer: Optional[str] = None
    has_answer: bool = False
    confidence: float = 0.0
    sources: List[Dict[str, Any]] = []
    chunks_used: int = 0
    suggested_followups: List[str] = []  # AI suggests follow-up questions


# ============================================================
# CONNECTORS
# ============================================================

class ConnectorType(str, Enum):
    GOOGLE_DRIVE = "google_drive"
    NOTION = "notion"
    SLACK = "slack"
    CONFLUENCE = "confluence"
    GITHUB = "github"
    JIRA = "jira"
    HUBSPOT = "hubspot"
    GMAIL = "gmail"
    ZENDESK = "zendesk"


class ConnectorCreate(BaseModel):
    connector_type: ConnectorType
    display_name: Optional[str] = None
    config: Dict[str, Any] = {}
    sync_frequency_minutes: int = 60


class ConnectorUpdate(BaseModel):
    display_name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    sync_frequency_minutes: Optional[int] = None


class ConnectorResponse(BaseModel):
    id: int
    connector_type: str
    display_name: Optional[str]
    status: str
    documents_indexed: int
    last_sync_at: Optional[datetime]
    last_sync_status: Optional[str]
    last_sync_message: Optional[str]
    sync_frequency_minutes: int
    config: Dict[str, Any] = {}
    created_at: datetime

    class Config:
        from_attributes = True


class ConnectorOAuthStart(BaseModel):
    connector_type: ConnectorType
    redirect_uri: Optional[str] = None


class ConnectorOAuthCallback(BaseModel):
    connector_type: ConnectorType
    code: str
    state: Optional[str] = None








# ============================================================
# HELP CENTER
# ============================================================

class HelpArticleCreate(BaseModel):
    title: str
    body: str
    category: Optional[str] = None
    section: Optional[str] = None
    tags: List[str] = []
    status: str = "draft"


class HelpArticleUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None
    section: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


class HelpArticleResponse(BaseModel):
    id: int
    title: str
    slug: str
    body: str
    category: Optional[str]
    section: Optional[str]
    tags: List[str] = []
    status: str
    view_count: int = 0
    helpful_count: int = 0
    not_helpful_count: int = 0
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ============================================================
# KNOWLEDGE HEALTH
# ============================================================

class KnowledgeHealthResponse(BaseModel):
    overall_score: float
    freshness_score: float
    total_documents: int
    stale_documents: int
    broken_links: int
    coverage_gaps: List[Dict[str, Any]]
    unused_documents: int
    details: Optional[Dict[str, Any]]
    generated_at: datetime





# ============================================================
# EXTENSION API
# ============================================================

class ExtensionContextRequest(BaseModel):
    """Context-aware search from the extension"""
    query: str
    app_context: Optional[str] = None  # gmail, github, jira, slack, etc.
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    selected_text: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ExtensionContextResponse(BaseModel):
    answer: Optional[str] = None
    has_answer: bool = False
    confidence: float = 0.0
    chunks: List[Dict[str, Any]] = []
    suggested_actions: List[Dict[str, Any]] = []  # context-specific actions