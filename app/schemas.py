"""
KRAB — Pydantic Schemas for API requests/responses
"""

from pydantic import BaseModel, EmailStr, Field
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
# TICKETS
# ============================================================

class TicketStatusEnum(str, Enum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    SOLVED = "solved"
    CLOSED = "closed"


class TicketPriorityEnum(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketChannelEnum(str, Enum):
    EMAIL = "email"
    WIDGET = "widget"
    API = "api"
    MANUAL = "manual"
    SLACK = "slack"


class TicketCreate(BaseModel):
    subject: str
    description: Optional[str] = None
    priority: TicketPriorityEnum = TicketPriorityEnum.NORMAL
    channel: TicketChannelEnum = TicketChannelEnum.MANUAL
    requester_email: Optional[str] = None
    requester_name: Optional[str] = None
    tags: List[str] = []
    assigned_agent_id: Optional[int] = None
    category: Optional[str] = None


class TicketUpdate(BaseModel):
    subject: Optional[str] = None
    status: Optional[TicketStatusEnum] = None
    priority: Optional[TicketPriorityEnum] = None
    assigned_agent_id: Optional[int] = None
    assigned_group: Optional[str] = None
    tags: Optional[List[str]] = None
    category: Optional[str] = None


class TicketCommentCreate(BaseModel):
    body: str
    is_internal: bool = False
    author_type: str = "agent"
    author_name: Optional[str] = None
    author_email: Optional[str] = None


class TicketCommentResponse(BaseModel):
    id: int
    ticket_id: int
    author_type: str
    author_name: Optional[str]
    author_email: Optional[str]
    body: str
    is_internal: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TicketEventResponse(BaseModel):
    id: int
    ticket_id: int
    event_type: str
    field_name: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    actor_type: str
    created_at: datetime

    class Config:
        from_attributes = True


class TicketResponse(BaseModel):
    id: int
    ticket_number: str
    subject: str
    description: Optional[str]
    status: str
    priority: str
    channel: str
    requester_email: Optional[str]
    requester_name: Optional[str]
    assigned_agent_id: Optional[int]
    assigned_group: Optional[str]
    tags: List[str] = []
    category: Optional[str]
    ai_intent: Optional[str]
    ai_sentiment: Optional[str]
    ai_confidence: Optional[float]
    ai_suggested_response: Optional[str]
    sla_breach: bool = False
    first_response_due_at: Optional[datetime]
    resolution_due_at: Optional[datetime]
    csat_rating: Optional[int]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    solved_at: Optional[datetime]
    comments: List[TicketCommentResponse] = []
    events: List[TicketEventResponse] = []

    class Config:
        from_attributes = True


class TicketListResponse(BaseModel):
    tickets: List[TicketResponse]
    total: int
    page: int
    page_size: int


class TicketListParams(BaseModel):
    status: Optional[TicketStatusEnum] = None
    priority: Optional[TicketPriorityEnum] = None
    assigned_agent_id: Optional[int] = None
    requester_email: Optional[str] = None
    channel: Optional[TicketChannelEnum] = None
    tag: Optional[str] = None
    search: Optional[str] = None
    page: int = 1
    page_size: int = 20
    sort_by: str = "created_at"
    sort_order: str = "desc"


class TicketAISuggest(BaseModel):
    """AI copilot suggestion for a ticket"""
    summary: str
    intent: str
    sentiment: str
    suggested_response: str
    confidence: float
    similar_tickets: List[Dict[str, Any]] = []
    relevant_articles: List[Dict[str, Any]] = []
    suggested_macros: List[Dict[str, Any]] = []


class TicketCSATRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = None


class TicketBulkUpdate(BaseModel):
    ticket_ids: List[int]
    status: Optional[TicketStatusEnum] = None
    priority: Optional[TicketPriorityEnum] = None
    assigned_agent_id: Optional[int] = None
    tags_add: List[str] = []
    tags_remove: List[str] = []


# ============================================================
# SLA
# ============================================================

class SLAPolicyCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_default: bool = False
    targets: Dict[str, Dict[str, int]]  # {"urgent": {"first_response": 30, "resolution": 240}}
    business_hours: Optional[Dict[str, Any]] = None


class SLAPolicyResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_default: bool
    targets: Dict[str, Dict[str, int]]
    business_hours: Optional[Dict[str, Any]]
    is_active: bool

    class Config:
        from_attributes = True


# ============================================================
# TRIGGERS & MACROS
# ============================================================

class TriggerCreate(BaseModel):
    name: str
    description: Optional[str] = None
    event: str  # ticket_created, ticket_updated
    conditions: Dict[str, Any]
    actions: List[Dict[str, Any]]


class TriggerResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    event: str
    conditions: Dict[str, Any]
    actions: List[Dict[str, Any]]
    is_active: bool
    position: int

    class Config:
        from_attributes = True


class MacroCreate(BaseModel):
    title: str
    description: Optional[str] = None
    actions: List[Dict[str, Any]]


class MacroResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    actions: List[Dict[str, Any]]
    usage_count: int
    is_active: bool

    class Config:
        from_attributes = True


class MacroApply(BaseModel):
    ticket_id: int
    macro_id: int


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
# ANALYTICS (enhanced)
# ============================================================

class TicketStatsResponse(BaseModel):
    total_tickets: int
    open_tickets: int
    pending_tickets: int
    solved_tickets: int
    avg_first_response_minutes: Optional[float]
    avg_resolution_minutes: Optional[float]
    sla_compliance_rate: Optional[float]
    csat_average: Optional[float]
    tickets_by_channel: Dict[str, int]
    tickets_by_priority: Dict[str, int]
    tickets_by_category: Dict[str, int]
    daily_volume: List[Dict[str, Any]]  # last 30 days


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
    related_tickets: List[Dict[str, Any]] = []