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