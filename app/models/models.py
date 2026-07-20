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


    user = relationship("User")


# ═══════════════════════════════════════════════════════════
#  KNOWLEDGE GRAPH (LCIE — Learning Content Intelligence)
# ═══════════════════════════════════════════════════════════

class KnowledgeNode(Base):
    """A concept vertex in the user's personal knowledge graph.
    Each node represents a single concept (e.g., 'Normalization', 'React Hooks').
    Nodes are deduplicated by canonical_name per user — never duplicated."""
    __tablename__ = "knowledge_nodes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Concept identity
    name = Column(String(255), nullable=False)              # Display name: "Normalization"
    canonical_name = Column(String(255), nullable=False)    # Lowercased/normalized for dedup: "normalization"
    category = Column(String(100), nullable=True)           # "DBMS", "Web Development", "DSA"

    # Graph metadata
    depth = Column(Integer, default=0)                      # 0=domain, 1=topic, 2=subtopic, 3=detail
    node_type = Column(String(30), default="concept")       # 'domain' | 'topic' | 'concept' | 'technique' | 'tool'

    # Knowledge state
    encounter_count = Column(Integer, default=1)            # How many times seen across sources
    confidence = Column(Float, default=0.0)                 # AI extraction confidence (validated)
    mastery_level = Column(Float, default=0.0)              # 0.0-1.0, grows with encounters + quiz scores
    first_seen_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)

    # Embedding for semantic matching (384-dim from all-MiniLM-L6-v2)
    embedding = Column(JSON, nullable=True)

    # Raw extracted text snippets (NOT summaries — for on-demand generation)
    context_snippets = Column(JSON, default=list)           # ["snippet from blog 1", "snippet from blog 2"]

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User")

    __table_args__ = (
        Index("idx_knode_user_canonical", "user_id", "canonical_name", unique=True),
        Index("idx_knode_user_category", "user_id", "category"),
        Index("idx_knode_user_type", "user_id", "node_type"),
    )


class KnowledgeEdge(Base):
    """A directed relationship between two knowledge nodes.
    Weight increases with repeated co-occurrence across sources."""
    __tablename__ = "knowledge_edges"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    source_node_id = Column(String(36), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False)
    target_node_id = Column(String(36), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False)

    # Relationship semantics
    relation_type = Column(String(30), nullable=False)      # 'contains' | 'requires' | 'related_to' | 'extends' | 'implements'
    weight = Column(Float, default=1.0)                     # Reinforced by repeated co-occurrence (max 5.0)

    # Provenance
    source_count = Column(Integer, default=1)               # How many sources established this edge

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    source_node = relationship("KnowledgeNode", foreign_keys=[source_node_id])
    target_node = relationship("KnowledgeNode", foreign_keys=[target_node_id])

    __table_args__ = (
        Index("idx_kedge_user_src_tgt", "user_id", "source_node_id", "target_node_id", unique=True),
        Index("idx_kedge_user_relation", "user_id", "relation_type"),
    )


class KnowledgeSource(Base):
    """A webpage/article that contributed knowledge to the graph.
    Stores provenance metadata — not full content. Raw text kept for on-demand summary generation."""
    __tablename__ = "knowledge_sources"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    url = Column(String(2000), nullable=False)
    url_hash = Column(String(64), nullable=False)           # SHA-256 of URL for fast dedup
    domain = Column(String(255), nullable=False)
    page_title = Column(String(500), nullable=False)

    # Content metadata
    content_type = Column(String(30), nullable=True)        # 'blog' | 'tutorial' | 'documentation' | 'research' | 'qa' | 'course'
    detected_language = Column(String(100), nullable=True)  # Programming languages detected (comma-separated)
    has_code_blocks = Column(Boolean, default=False)
    estimated_reading_minutes = Column(Integer, default=0)

    # Learning intent (rule-based + AI inferred)
    learning_intent = Column(String(30), nullable=True)     # 'learning' | 'interview_prep' | 'debugging' | 'revision' | etc.

    # Extraction metadata
    extraction_method = Column(String(20), default="hybrid") # 'rule_based' | 'ai' | 'hybrid'
    ai_confidence = Column(Float, default=0.0)
    raw_extracted_text = Column(Text, nullable=True)         # First 3000 chars for on-demand summary
    rule_extracted_data = Column(JSON, default=dict)         # {languages: [...], technologies: [...], headings: [...]}

    # Linked nodes (which concepts this source contributed to)
    node_ids = Column(JSON, default=list)                    # ["node-uuid-1", "node-uuid-2"]

    analyzed_at = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User")

    __table_args__ = (
        Index("idx_ksource_user_hash", "user_id", "url_hash", unique=True),
        Index("idx_ksource_user_domain", "user_id", "domain"),
        Index("idx_ksource_user_intent", "user_id", "learning_intent"),
        Index("idx_ksource_user_created", "user_id", "created_at"),
    )


class LearningSession(Base):
    """A learning session linking time-on-page to knowledge nodes.
    Bridges tracking data with the knowledge graph."""
    __tablename__ = "learning_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id = Column(String(36), ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False)

    # Time tracking
    started_at = Column(DateTime, nullable=False, default=utcnow)
    duration_seconds = Column(Integer, default=0)

    # Learning context
    learning_intent = Column(String(30), nullable=True)
    concepts_encountered = Column(JSON, default=list)       # Concept names from this session

    created_at = Column(DateTime, default=utcnow)

    source = relationship("KnowledgeSource")
    user = relationship("User")

    __table_args__ = (
        Index("idx_lsession_user_created", "user_id", "created_at"),
    )

# ═══════════════════════════════════════════════════════════
#  LEARNING PATH DISCOVERY ENGINE
# ═══════════════════════════════════════════════════════════

class LearningPath(Base):
    """An automatically inferred learning path discovered from the knowledge graph.
    Never manually created — always algorithmically detected."""
    __tablename__ = "learning_paths"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Path identity
    path_name = Column(String(255), nullable=False)            # "Backend Development", "Machine Learning"
    path_slug = Column(String(255), nullable=False)            # Lowercase slug for dedup
    description = Column(Text, nullable=True)                  # AI-generated description

    # Discovery metadata
    confidence = Column(Float, default=0.0)                    # 0.0-1.0 how confident is the inference
    status = Column(String(30), default="growing")             # 'growing' | 'mature' | 'completed' | 'stale'
    is_primary = Column(Boolean, default=False)                # True if this is the dominant learning path

    # Progress
    stage = Column(String(30), default="beginner")             # 'beginner' | 'intermediate' | 'advanced' | 'expert'
    completion_pct = Column(Float, default=0.0)                # 0-100
    total_concepts = Column(Integer, default=0)
    mastered_concepts = Column(Integer, default=0)

    # AI-inferred data (stored as JSON)
    missing_topics = Column(JSON, default=list)                # ["Docker", "Redis", "Kubernetes"]
    milestone_history = Column(JSON, default=list)             # [{concept, date, order}]

    detected_at = Column(DateTime, default=utcnow)
    last_updated = Column(DateTime, default=utcnow, onupdate=utcnow)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User")
    path_nodes = relationship("LearningPathNode", back_populates="learning_path", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_lpath_user_slug", "user_id", "path_slug", unique=True),
        Index("idx_lpath_user_primary", "user_id", "is_primary"),
        Index("idx_lpath_user_confidence", "user_id", "confidence"),
    )


class LearningPathNode(Base):
    """Links a knowledge node to a learning path with ordering and importance."""
    __tablename__ = "learning_path_nodes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    learning_path_id = Column(String(36), ForeignKey("learning_paths.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_node_id = Column(String(36), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False)

    # Ordering and importance
    learning_order = Column(Integer, default=0)                # Sequential order in the path
    importance_score = Column(Float, default=0.5)              # 0.0-1.0 how critical this node is

    # Status
    is_completed = Column(Boolean, default=False)              # Based on mastery_level threshold

    created_at = Column(DateTime, default=utcnow)

    learning_path = relationship("LearningPath", back_populates="path_nodes")
    knowledge_node = relationship("KnowledgeNode")

    __table_args__ = (
        Index("idx_lpnode_path_node", "learning_path_id", "knowledge_node_id", unique=True),
        Index("idx_lpnode_path_order", "learning_path_id", "learning_order"),
    )


# ═══════════════════════════════════════════════════════════
#  KNOWLEDGE GAP DETECTION ENGINE
# ═══════════════════════════════════════════════════════════

class KnowledgeGap(Base):
    """A detected knowledge gap — a missing prerequisite concept
    inferred from the Knowledge Graph and Learning Paths.
    Never manually created — always algorithmically or AI-detected."""
    __tablename__ = "knowledge_gaps"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Gap identity (deduplicated by canonical_concept per user)
    concept = Column(String(255), nullable=False)               # "HTTP Fundamentals", "NumPy"
    canonical_concept = Column(String(255), nullable=False)     # Lowered slug for dedup
    category = Column(String(100), nullable=True)               # "Networking", "Mathematics"

    # Related learning path
    learning_path_id = Column(String(36), ForeignKey("learning_paths.id", ondelete="SET NULL"), nullable=True)
    learning_path_name = Column(String(255), nullable=True)     # Denormalized for fast reads

    # Severity & priority
    severity = Column(Float, default=0.5)                       # 0.0-1.0 how critical the gap is
    priority = Column(String(20), default="medium")             # 'critical' | 'high' | 'medium' | 'low'
    confidence = Column(Float, default=0.5)                     # 0.0-1.0 AI detection confidence

    # Gap reason (human-readable)
    reason = Column(Text, nullable=True)                        # "REST APIs rely heavily on HTTP fundamentals."
    detection_method = Column(String(30), default="hybrid")     # 'graph' | 'ai' | 'hybrid' | 'prerequisite_chain'

    # Dependency chain (JSON: list of concept names this gap blocks)
    blocks_concepts = Column(JSON, default=list)                # ["REST API", "Spring Boot"]
    prerequisite_of = Column(JSON, default=list)                # Which known concepts need this

    # Status lifecycle
    status = Column(String(20), default="detected")             # 'detected' | 'learning' | 'resolved' | 'dismissed'
    resolved_at = Column(DateTime, nullable=True)

    # Estimation
    estimated_study_minutes = Column(Integer, default=30)
    difficulty = Column(String(20), default="intermediate")     # 'beginner' | 'intermediate' | 'advanced'

    detected_at = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User")
    learning_path = relationship("LearningPath")
    recommendations = relationship("KnowledgeGapRecommendation", back_populates="gap", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_kgap_user_canonical", "user_id", "canonical_concept", unique=True),
        Index("idx_kgap_user_status", "user_id", "status"),
        Index("idx_kgap_user_priority", "user_id", "priority"),
        Index("idx_kgap_user_severity", "user_id", "severity"),
        Index("idx_kgap_user_path", "user_id", "learning_path_id"),
    )


class KnowledgeGapRecommendation(Base):
    """A study recommendation for resolving a knowledge gap.
    Generated on demand — never stored preemptively unless gap detection runs."""
    __tablename__ = "knowledge_gap_recommendations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    gap_id = Column(String(36), ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), nullable=False, index=True)

    # Recommendation content
    resource_type = Column(String(30), nullable=False)          # 'tutorial' | 'documentation' | 'video' | 'practice' | 'article'
    title = Column(String(500), nullable=True)                  # "Learn HTTP Fundamentals"
    description = Column(Text, nullable=True)                   # Why this resource helps
    estimated_minutes = Column(Integer, default=30)
    difficulty = Column(String(20), default="intermediate")

    # Prioritization
    relevance_score = Column(Float, default=0.5)                # 0.0-1.0 how relevant to closing the gap
    confidence = Column(Float, default=0.5)

    created_at = Column(DateTime, default=utcnow)

    gap = relationship("KnowledgeGap", back_populates="recommendations")

    __table_args__ = (
        Index("idx_kgaprec_gap", "gap_id"),
    )


# ═══════════════════════════════════════════════════════════
# END MODELS
# ═══════════════════════════════════════════════════════════
