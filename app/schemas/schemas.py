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
    # Extension-side YouTube video classification (productive/distracting/neutral).
    # When present, this takes priority over domain-level defaults for youtube.com.
    yt_classification: Optional[str] = Field(None, max_length=20)


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


# ═══════════════════════════════════════════════════════════
#  INTEGRATIONS: GOOGLE CALENDAR & NOTION
# ═══════════════════════════════════════════════════════════

class GoogleCalendarAuthRequest(BaseModel):
    code: str = Field(..., description="OAuth authorization code")


class SyncStudySessionRequest(BaseModel):
    title: str = Field(..., min_length=1)
    start_time: datetime
    end_time: datetime
    description: Optional[str] = None
    study_plan_id: Optional[str] = None


class SyncDeadlineRequest(BaseModel):
    title: str = Field(..., min_length=1)
    deadline: datetime
    description: Optional[str] = None
    study_plan_id: Optional[str] = None
    chapter_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════
#  KNOWLEDGE INTELLIGENCE (LCIE)
# ═══════════════════════════════════════════════════════════

class KnowledgeIngestRequest(BaseModel):
    """Raw educational content from extension for Knowledge Graph processing."""
    url: str = Field(..., max_length=2000)
    domain: str = Field(..., max_length=255)
    page_title: str = Field(..., max_length=500)
    content: str = Field(..., max_length=5000)
    headings: List[str] = Field(default_factory=list)
    detected_languages: List[str] = Field(default_factory=list)
    detected_technologies: List[str] = Field(default_factory=list)
    learning_intent: Optional[str] = Field(None, max_length=30)
    estimated_reading_minutes: int = Field(0, ge=0)
    source_type: Optional[str] = Field(None, max_length=30)
    detection_confidence: float = Field(0.5, ge=0.0, le=1.0)


class KnowledgeNodeResponse(BaseModel):
    id: str
    name: str
    canonical_name: str
    category: Optional[str] = None
    depth: int
    node_type: str
    encounter_count: int
    confidence: float
    mastery_level: float
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class KnowledgeEdgeResponse(BaseModel):
    id: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    weight: float
    source_count: int

    class Config:
        from_attributes = True


class KnowledgeSourceResponse(BaseModel):
    id: str
    url: str
    domain: str
    page_title: str
    content_type: Optional[str] = None
    detected_language: Optional[str] = None
    has_code_blocks: bool
    estimated_reading_minutes: int
    learning_intent: Optional[str] = None
    extraction_method: str
    ai_confidence: float
    node_ids: list
    analyzed_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class LearningSessionResponse(BaseModel):
    id: str
    source_id: str
    started_at: datetime
    duration_seconds: int
    learning_intent: Optional[str] = None
    concepts_encountered: list
    created_at: datetime

    class Config:
        from_attributes = True


class KnowledgeGraphResponse(BaseModel):
    nodes: List[KnowledgeNodeResponse]
    edges: List[KnowledgeEdgeResponse]
    stats: dict


class KnowledgeStatsResponse(BaseModel):
    total_nodes: int
    total_edges: int
    total_sources: int
    total_learning_minutes: int
    categories: List[dict]
    intent_distribution: dict
    difficulty_distribution: dict
    top_concepts: List[dict]
    mastery_overview: dict


# ═══════════════════════════════════════════════════════════
#  LEARNING PATH DISCOVERY ENGINE
# ═══════════════════════════════════════════════════════════

class LearningPathNodeResponse(BaseModel):
    id: str
    knowledge_node_id: str
    node_name: Optional[str] = None
    node_category: Optional[str] = None
    node_mastery: Optional[float] = 0.0
    learning_order: int
    importance_score: float
    is_completed: bool

    class Config:
        from_attributes = True


class LearningPathResponse(BaseModel):
    id: str
    path_name: str
    path_slug: str
    description: Optional[str] = None
    confidence: float
    status: str
    is_primary: bool
    stage: str
    completion_pct: float
    total_concepts: int
    mastered_concepts: int
    missing_topics: list
    milestone_history: list
    detected_at: datetime
    last_updated: datetime
    nodes: List[LearningPathNodeResponse] = []

    class Config:
        from_attributes = True


class LearningPathSummaryResponse(BaseModel):
    primary_path: Optional[str] = None
    secondary_paths: List[str] = []
    confidence: float = 0.0
    stage: str = "beginner"
    completion: float = 0.0
    missing_topics: List[str] = []
    total_paths: int = 0
    last_analysis: Optional[datetime] = None


class LearningPathHistoryEntry(BaseModel):
    path_name: str
    confidence: float
    status: str
    stage: str
    completion_pct: float
    detected_at: datetime
    last_updated: datetime
    node_count: int


# ═══════════════════════════════════════════════════════════
#  KNOWLEDGE GAP DETECTION ENGINE
# ═══════════════════════════════════════════════════════════

class KnowledgeGapRecommendationResponse(BaseModel):
    id: str
    resource_type: str
    title: Optional[str] = None
    description: Optional[str] = None
    estimated_minutes: int
    difficulty: str
    relevance_score: float
    confidence: float
    created_at: datetime

    class Config:
        from_attributes = True


class KnowledgeGapResponse(BaseModel):
    id: str
    concept: str
    canonical_concept: str
    category: Optional[str] = None
    learning_path_id: Optional[str] = None
    learning_path_name: Optional[str] = None
    severity: float
    priority: str
    confidence: float
    reason: Optional[str] = None
    detection_method: str
    blocks_concepts: list
    prerequisite_of: list
    status: str
    resolved_at: Optional[datetime] = None
    estimated_study_minutes: int
    difficulty: str
    detected_at: datetime
    created_at: datetime
    updated_at: datetime
    recommendations: List[KnowledgeGapRecommendationResponse] = []

    class Config:
        from_attributes = True


class KnowledgeGapSummaryResponse(BaseModel):
    total_gaps: int
    critical_gaps: int
    high_gaps: int
    medium_gaps: int
    low_gaps: int
    resolved_gaps: int
    avg_severity: float
    most_impacted_path: Optional[str] = None
    top_gaps: List[dict] = []
    last_analysis: Optional[datetime] = None


class KnowledgeGapHistoryEntry(BaseModel):
    concept: str
    learning_path_name: Optional[str] = None
    severity: float
    priority: str
    status: str
    detected_at: datetime
    resolved_at: Optional[datetime] = None

