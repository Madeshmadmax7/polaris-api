"""
LifeOS – Pydantic Schemas for request/response validation.
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum


# ═══════════════════════════════════════════════════════════
#  ENUMS
# ═══════════════════════════════════════════════════════════

class UserRole(str, Enum):
    student = "student"
    parent = "parent"


class SiteCategory(str, Enum):
    productive = "productive"
    neutral = "neutral"
    distracting = "distracting"


class QuizDifficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


# ═══════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.student


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ═══════════════════════════════════════════════════════════
#  TRACKING
# ═══════════════════════════════════════════════════════════

class TrackingLogCreate(BaseModel):
    domain: str = Field(..., max_length=255)
    duration_seconds: int = Field(..., ge=0)
    tab_switches: int = Field(0, ge=0)
    scroll_depth: float = Field(0.0, ge=0.0, le=1.0)
    is_active: bool = True
    page_title: Optional[str] = Field(None, max_length=500)
    timestamp: Optional[datetime] = None


class TrackingBatchCreate(BaseModel):
    """Batch of tracking logs from extension offline buffer."""
    logs: List[TrackingLogCreate]


class TrackingLogResponse(BaseModel):
    id: str
    domain: str
    category: SiteCategory
    duration_seconds: int
    tab_switches: int
    is_active: bool
    page_title: Optional[str] = None
    timestamp: datetime

    class Config:
        from_attributes = True


# ═══════════════════════════════════════════════════════════
#  PRODUCTIVITY
# ═══════════════════════════════════════════════════════════

class ProductivityScoreResponse(BaseModel):
    date: str
    productivity_score: float
    focus_factor: float
    total_active_minutes: float
    productive_minutes: float
    distracting_minutes: float
    tab_switches: int
    quiz_average: float
    top_domains: List[dict]


class ProductivityTrendResponse(BaseModel):
    scores: List[ProductivityScoreResponse]
    average_score: float
    trend: str  # "improving" | "declining" | "stable"


# ═══════════════════════════════════════════════════════════
#  PARENTAL CONTROL
# ═══════════════════════════════════════════════════════════

class LinkChildRequest(BaseModel):
    invite_code: str


class GenerateInviteResponse(BaseModel):
    invite_code: str
    expires_at: Optional[datetime] = None


class BlockSiteRequest(BaseModel):
    child_id: str
    domain: str


class BlockedSiteResponse(BaseModel):
    id: str
    domain: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ChildOverview(BaseModel):
    child_id: str
    username: str
    productivity_score: float
    focus_factor: float
    total_active_minutes: float
    top_domains: List[dict]
    blocked_sites: List[BlockedSiteResponse]


# ═══════════════════════════════════════════════════════════
#  RAG / DOCUMENTS
# ═══════════════════════════════════════════════════════════

class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    created_at: datetime

    class Config:
        from_attributes = True


class RAGQueryRequest(BaseModel):
    query: str = Field(..., min_length=5)
    document_id: Optional[str] = None
    top_k: int = Field(5, ge=1, le=20)


class RAGQueryResponse(BaseModel):
    answer: str
    sources: List[dict]
    confidence: float


# ═══════════════════════════════════════════════════════════
#  STUDY PLAN
# ═══════════════════════════════════════════════════════════

class StudyPlanRequest(BaseModel):
    goal: str = Field(..., min_length=5)
    duration_days: int = Field(..., ge=1, le=365)
    document_id: Optional[str] = None


class StudyPlanResponse(BaseModel):
    id: str
    title: str
    goal: str
    plan_data: Any
    duration_days: int
    quiz_unlocked: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ═══════════════════════════════════════════════════════════
#  QUIZ ENGINE
# ═══════════════════════════════════════════════════════════

class QuizGenerateRequest(BaseModel):
    study_plan_id: Optional[str] = None
    topic: Optional[str] = None
    difficulty: QuizDifficulty = QuizDifficulty.medium
    document_id: Optional[str] = None


class QuizSubmitRequest(BaseModel):
    quiz_id: str
    answers: dict


class QuizResponse(BaseModel):
    id: str
    questions: Any
    difficulty: QuizDifficulty
    score: Optional[float] = None
    max_score: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ═══════════════════════════════════════════════════════════
#  DOMAIN CATEGORY
# ═══════════════════════════════════════════════════════════

class DomainCategoryCreate(BaseModel):
    domain_pattern: str
    category: SiteCategory


class DomainCategoryResponse(BaseModel):
    id: str
    domain_pattern: str
    category: SiteCategory
    is_global: bool

    class Config:
        from_attributes = True
