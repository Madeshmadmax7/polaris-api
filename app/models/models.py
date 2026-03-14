"""
LifeOS – SQLAlchemy Database Models
Implements CASCADE deletes, indexed timestamps, and UTC-aware fields.
Compatible with both SQLite (dev) and MySQL (production).
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, DateTime,
    ForeignKey, Index, JSON
)
from sqlalchemy.orm import relationship
from app.config.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════
#  USER & AUTH
# ═══════════════════════════════════════════════════════════

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="student")  # 'student' | 'parent'
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    tracking_logs = relationship("TrackingLog", back_populates="user", cascade="all, delete-orphan")
    daily_summaries = relationship("DailySummary", back_populates="user", cascade="all, delete-orphan")
    study_plans = relationship("StudyPlan", back_populates="user", cascade="all, delete-orphan")
    quiz_attempts = relationship("QuizAttempt", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    children = relationship("ParentChild", back_populates="parent",
                            foreign_keys="ParentChild.parent_id", cascade="all, delete-orphan")
    parents = relationship("ParentChild", back_populates="child",
                           foreign_keys="ParentChild.child_id", cascade="all, delete-orphan")


# ═══════════════════════════════════════════════════════════
#  PARENTAL CONTROL
# ═══════════════════════════════════════════════════════════

class ParentChild(Base):
    __tablename__ = "parent_child"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    parent_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    child_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    invite_code = Column(String(20), unique=True)
    is_accepted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

    parent = relationship("User", back_populates="children", foreign_keys=[parent_id])
    child = relationship("User", back_populates="parents", foreign_keys=[child_id])


class ParentChildConnection(Base):
    """OTP-based parent-child verification for analytics access."""
    __tablename__ = "parent_child_connections"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    parent_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    child_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    otp_code = Column(String(4), nullable=False)
    otp_created_at = Column(DateTime, default=utcnow, nullable=False)
    verified = Column(Boolean, default=False)
    connected_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="pending")  # 'pending' | 'active' | 'expired'
    created_at = Column(DateTime, default=utcnow)

    parent = relationship("User", foreign_keys=[parent_id])
    child = relationship("User", foreign_keys=[child_id])

    __table_args__ = (
        Index("idx_parent_child_status", "parent_id", "child_id", "status"),
        Index("idx_parent_child_connection", "parent_id", "child_id"),
    )


class BlockedSite(Base):
    __tablename__ = "blocked_sites"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    parent_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    child_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    domain = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index("idx_blocked_child_domain", "child_id", "domain"),
    )


# ═══════════════════════════════════════════════════════════
#  ACTIVITY TRACKING
# ═══════════════════════════════════════════════════════════

class TrackingLog(Base):
    __tablename__ = "tracking_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    domain = Column(String(255), nullable=False)
    category = Column(String(20), default="neutral")  # 'productive' | 'neutral' | 'distracting'
    duration_seconds = Column(Integer, nullable=False, default=0)
    tab_switches = Column(Integer, default=0)
    scroll_depth = Column(Float, default=0.0)
    is_active = Column(Boolean, default=True)  # Was user active during this period?
    page_title = Column(String(500), nullable=True)  # Video/page title (YouTube only)
    timestamp = Column(DateTime, default=utcnow, index=True)

    user = relationship("User", back_populates="tracking_logs")

    __table_args__ = (
        Index("idx_tracking_user_timestamp", "user_id", "timestamp"),
        Index("idx_tracking_user_domain", "user_id", "domain"),
    )


class DailySummary(Base):
    """Pre-aggregated daily summary for fast analytics queries."""
    __tablename__ = "daily_summaries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    total_active_seconds = Column(Integer, default=0)
    productive_seconds = Column(Integer, default=0)
    neutral_seconds = Column(Integer, default=0)
    distracting_seconds = Column(Integer, default=0)
    total_tab_switches = Column(Integer, default=0)
    focus_factor = Column(Float, default=1.0)
    productivity_score = Column(Float, default=0.0)
    quiz_average = Column(Float, default=0.0)
    top_domains = Column(JSON, default=list)

    user = relationship("User", back_populates="daily_summaries")

    __table_args__ = (
        Index("idx_summary_user_date", "user_id", "date", unique=True),
    )


# ═══════════════════════════════════════════════════════════
#  DOMAIN CLASSIFICATIONS
# ═══════════════════════════════════════════════════════════

class DomainCategory(Base):
    """Configurable domain → category mapping."""
    __tablename__ = "domain_categories"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    domain_pattern = Column(String(255), nullable=False, unique=True)
    category = Column(String(20), nullable=False)  # 'productive' | 'neutral' | 'distracting'
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    is_global = Column(Boolean, default=False)


# ═══════════════════════════════════════════════════════════
#  RAG & STUDY PLANNING
# ═══════════════════════════════════════════════════════════

class Document(Base):
    """Uploaded syllabus / curriculum / exam outline."""
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(50), default="pdf")
    content = Column(Text, nullable=True)  # Full extracted text
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="documents")


class ChapterProgress(Base):
    """Tracks completion of study plan chapters (YouTube videos)."""
    __tablename__ = "chapter_progress"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    study_plan_id = Column(String(36), ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_index = Column(Integer, nullable=False)
    chapter_title = Column(String(255), nullable=False)
    youtube_url = Column(String(500), nullable=True)
    youtube_title = Column(String(500), nullable=True)  # Actual YouTube video title
    video_duration_seconds = Column(Integer, default=0)  # Total video duration from YouTube
    watched_seconds = Column(Integer, default=0)  # User's watch progress
    creator_name = Column(String(100), nullable=True)  # e.g., "striver", "kunal kushwaha"
    keyword_importance = Column(JSON, nullable=True)  # AI-generated word importance scores {"fibonacci": 100, "dp": 80, "striver": 10}
    chapter_embedding = Column(JSON, nullable=True)   # 384-dim float list from all-MiniLM-L6-v2; NULL for legacy rows
    playback_rate = Column(Float, default=1.0)   # Speed user watches at (1.0=normal, 2.0=2x)
    ai_summary = Column(Text, nullable=True)        # AI-generated summary after completion
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    study_plan = relationship("StudyPlan")
    user = relationship("User")

    __table_args__ = (
        Index("idx_chapter_progress", "study_plan_id", "chapter_index", unique=True),
    )


class StudyPlan(Base):
    __tablename__ = "study_plans"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    goal = Column(Text)
    plan_data = Column(JSON, nullable=False)  # Contains: chapters (with YouTube links) + quiz
    duration_days = Column(Integer)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    quiz_unlocked = Column(Boolean, default=False)  # Unlocked after all chapters completed
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="study_plans")


# ═══════════════════════════════════════════════════════════
#  QUIZ ENGINE
# ═══════════════════════════════════════════════════════════

class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    study_plan_id = Column(String(36), ForeignKey("study_plans.id", ondelete="SET NULL"), nullable=True)
    questions = Column(JSON, nullable=False)
    answers = Column(JSON, default=dict)
    score = Column(Float, default=0.0)
    max_score = Column(Float, default=0.0)
    difficulty = Column(String(20), default="medium")  # 'easy' | 'medium' | 'hard'
    focus_minutes_before = Column(Float, default=0.0)  # Must meet minimum
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="quiz_attempts")

    __table_args__ = (
        Index("idx_quiz_user_created", "user_id", "created_at"),
    )


# ═══════════════════════════════════════════════════════════
#  LEARNER PROFILE (cross-plan knowledge memory)
# ═══════════════════════════════════════════════════════════

class LearnerProfile(Base):
    """Compact cross-plan learner profile.
    Updated after every quiz submission so the next study plan
    skips already-mastered topics and reinforces weak ones."""
    __tablename__ = "learner_profile"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # Chapter titles from plans where quiz score >= 60 (topics the learner knows)
    mastered_topics = Column(JSON, default=list)
    # Plan titles where the learner scored < 60 (need reinforcement)
    weak_plan_topics = Column(JSON, default=list)
    # Rolling average quiz score across all plans
    avg_quiz_score = Column(Float, default=0.0)
    # [{"title": str, "score": float, "chapters": int}]
    completed_plans = Column(JSON, default=list)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# ═══════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════

class Notification(Base):
    """In-app notifications for users."""
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(50), nullable=False)  # 'parent_connection_request' | 'connection_verified' | 'connection_expired' | etc.
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    data = Column(JSON, default=dict)  # Extra data like connection_id, child_name, etc.
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow, index=True)

    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_notification_user_read", "user_id", "is_read"),
    )
