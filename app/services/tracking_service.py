"""
LifeOS – Tracking Service
Handles activity log ingestion with privacy filtering and category resolution.
"""

from datetime import datetime, timezone
from typing import List, Optional
from functools import lru_cache
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import TrackingLog, DomainCategory
from app.schemas.schemas import TrackingLogCreate
from app.utils.privacy import sanitize_url


# ── Default Domain Classifications ───────────────────────
DEFAULT_CATEGORIES = {
    # Productive
    "github.com": "productive",
    "stackoverflow.com": "productive",
    "docs.python.org": "productive",
    "developer.mozilla.org": "productive",
    "leetcode.com": "productive",
    "hackerrank.com": "productive",
    "coursera.org": "productive",
    "udemy.com": "productive",
    "edx.org": "productive",
    "khanacademy.org": "productive",
    "scholar.google.com": "productive",
    "arxiv.org": "productive",
    "notion.so": "productive",
    "docs.google.com": "productive",
    "kaggle.com": "productive",
    "replit.com": "productive",
    "codepen.io": "productive",

    # Distracting
    "youtube.com": "distracting",
    "reddit.com": "distracting",
    "twitter.com": "distracting",
    "x.com": "distracting",
    "instagram.com": "distracting",
    "facebook.com": "distracting",
    "tiktok.com": "distracting",
    "twitch.tv": "distracting",
    "9gag.com": "distracting",
    "buzzfeed.com": "distracting",
    "netflix.com": "distracting",
    "primevideo.com": "distracting",
    "hotstar.com": "distracting",
}


_defaults_seeded = False

def resolve_category(domain: str, db: Session, user_id: str) -> str:
    """Resolve domain category: user override > global > default > neutral."""
    global _defaults_seeded
    if not _defaults_seeded:
        try:
            # Seed defaults as global if they don't exist
            for pat, cat in DEFAULT_CATEGORIES.items():
                exists = db.query(DomainCategory).filter(
                    DomainCategory.domain_pattern == pat, 
                    DomainCategory.is_global == True
                ).first()
                if not exists:
                    db.add(DomainCategory(domain_pattern=pat, category=cat, is_global=True, user_id="system"))
            db.commit()
        except Exception:
            db.rollback()
        _defaults_seeded = True

    # Check user-specific override
    user_cat = db.query(DomainCategory).filter(
        DomainCategory.domain_pattern == domain,
        DomainCategory.user_id == user_id
    ).first()
    if user_cat:
        return user_cat.category

    # Check global classification
    global_cat = db.query(DomainCategory).filter(
        DomainCategory.domain_pattern == domain,
        DomainCategory.is_global == True
    ).first()
    if global_cat:
        return global_cat.category

    # Check defaults
    if domain in DEFAULT_CATEGORIES:
        return DEFAULT_CATEGORIES[domain]

    # Check partial match (e.g. "docs.python.org" matches "python.org")
    for pattern, category in DEFAULT_CATEGORIES.items():
        if domain.endswith(f".{pattern}") or domain == pattern:
            return category

    return "neutral"


def ingest_tracking_log(
    db: Session,
    user_id: str,
    log_data: TrackingLogCreate,
) -> TrackingLog:
    """Ingest a single tracking log with privacy filtering and category resolution."""
    page_title = getattr(log_data, 'page_title', None)
    yt_cls = getattr(log_data, 'yt_classification', None)

    # ── Desktop Activity (sent by the desktop_tracker Python agent) ──────────
    # Domain format: "desktop://Visual Studio Code"
    # The tracker pre-classifies via yt_classification, so we skip
    # URL sanitisation and domain-based category lookup entirely.
    if log_data.domain.startswith("desktop://"):
        domain = log_data.domain
        category = yt_cls if yt_cls in ('productive', 'neutral', 'distracting') else 'neutral'
    else:
        # ── Web Activity (sent by the Chrome extension) ───────────────────────
        domain = sanitize_url(f"https://{log_data.domain}")
        if not domain:
            domain = log_data.domain  # Fallback if already a clean hostname

        category = resolve_category(domain, db, user_id)

        # Smart YouTube classification — three-tier override:
        # 1. Trust the extension's real-time classification if provided (most accurate).
        # 2. Fall back to title keyword matching.
        # 3. Otherwise keep domain default.
        if domain in ('youtube.com', 'youtu.be'):
            if yt_cls in ('productive', 'neutral', 'distracting'):
                # Extension already classified this video — trust it directly.
                category = yt_cls
            elif page_title and category == 'distracting' and _is_learning_video(page_title):
                category = 'productive'

    log = TrackingLog(
        user_id=user_id,
        domain=domain,
        category=category,
        duration_seconds=log_data.duration_seconds,
        tab_switches=log_data.tab_switches,
        scroll_depth=log_data.scroll_depth,
        is_active=log_data.is_active,
        page_title=page_title,
        timestamp=log_data.timestamp or datetime.now(timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


# ── YouTube Learning Detection ────────────────────────────
LEARNING_KEYWORDS = [
    # General education
    'tutorial', 'course', 'lecture', 'learn', 'how to', 'explained',
    'walkthrough', 'study', 'education', 'class', 'lesson', 'training',
    'guide', 'documentation', 'workshop', 'bootcamp', 'masterclass',
    'introduction', 'intro', 'beginner', 'basics', 'fundamentals', 'overview',
    'crash course', 'complete guide', 'full course', 'for beginners', 'step by step',
    # Web fundamentals (titles like "Semantic HTML & Accessibility")
    'html', 'css', 'web design', 'web development', 'web dev', 'webpage',
    'website', 'markup', 'styling', 'responsive', 'flexbox', 'grid layout',
    'bootstrap', 'tailwind', 'sass', 'scss', 'dom', 'semantic',
    'accessibility', 'forms', 'tables', 'tags', 'attributes', 'selectors',
    'w3schools', 'mdn', 'web standards',
    # CS / Programming
    'programming', 'coding', 'code', 'developer', 'software',
    'javascript', 'python', 'java', 'react', 'node', 'sql', 'database',
    'algorithm', 'data structure', 'dsa', 'leetcode', 'competitive',
    'frontend', 'backend', 'fullstack', 'full stack', 'api', 'devops',
    'git', 'linux', 'docker', 'kubernetes', 'cloud', 'aws', 'azure',
    'typescript', 'c++', 'golang', 'rust', 'flutter', 'swift',
    'machine learning', 'deep learning', 'artificial intelligence',
    'neural network', 'nlp', 'computer vision', 'tensorflow', 'pytorch',
    'hooks', 'context api', 'state management', 'lifecycle', 'component',
    'express', 'mongodb', 'postgresql', 'redis', 'graphql',
    'rest api', 'microservices', 'ci/cd', 'testing', 'debugging',
    'object oriented', 'functional programming', 'async', 'promises',
    'data science', 'pandas', 'numpy', 'matplotlib', 'jupyter',
    'cybersecurity', 'networking', 'operating system', 'compiler',
    # Science & Math
    'physics', 'chemistry', 'biology', 'math', 'calculus', 'algebra',
    'statistics', 'probability', 'engineering', 'science',
    # Academic
    'exam', 'preparation', 'gate', 'placement', 'interview prep',
    'campus', 'semester', 'university', 'college', 'syllabus',
]

GAMING_KEYWORDS = [
    'gameplay', 'walkthrough', 'speedrun', "let's play", 'playthrough', 'montage', 'highlights',
    'gta', 'valorant', 'minecraft', 'roblox', 'fortnite', 'apex legends', 'warzone', 'cod', 'csgo'
]

@lru_cache(maxsize=1000)
def _ai_classify_youtube(title: str) -> bool:
    try:
        from app.services.ai_service import classify_desktop_app
        return classify_desktop_app("YouTube Video", title) == "productive"
    except Exception:
        return False


def _is_learning_video(title: str) -> bool:
    """Check if a YouTube video title indicates educational content."""
    lower_title = title.lower()
    
    # Fast reject if it has gaming keywords
    if any(keyword in lower_title for keyword in GAMING_KEYWORDS):
        return False
        
    # Fast accept if it has strong learning keywords
    if any(keyword in lower_title for keyword in LEARNING_KEYWORDS):
        return True
        
    # LLM semantic fallback
    return _ai_classify_youtube(title)


def ingest_batch(
    db: Session,
    user_id: str,
    logs: List[TrackingLogCreate],
) -> int:
    """Ingest a batch of tracking logs (from extension offline buffer). Returns count."""
    count = 0
    for log_data in logs:
        try:
            ingest_tracking_log(db, user_id, log_data)
            count += 1
        except Exception:
            db.rollback()
            continue
    return count


def get_user_logs(
    db: Session,
    user_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 100,
) -> List[TrackingLog]:
    """Retrieve tracking logs for a user within a date range."""
    query = db.query(TrackingLog).filter(TrackingLog.user_id == user_id)
    if start:
        query = query.filter(TrackingLog.timestamp >= start)
    if end:
        query = query.filter(TrackingLog.timestamp <= end)
    return query.order_by(TrackingLog.timestamp.desc()).limit(limit).all()


def get_domain_breakdown(
    db: Session,
    user_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[dict]:
    """Get time spent per domain, grouped and sorted."""
    query = db.query(
        TrackingLog.domain,
        TrackingLog.category,
        func.sum(TrackingLog.duration_seconds).label("total_seconds"),
        func.count(TrackingLog.id).label("visit_count"),
    ).filter(
        TrackingLog.user_id == user_id,
    ).group_by(TrackingLog.domain, TrackingLog.category)

    if start:
        query = query.filter(TrackingLog.timestamp >= start)
    if end:
        query = query.filter(TrackingLog.timestamp <= end)

    results = query.order_by(func.sum(TrackingLog.duration_seconds).desc()).all()

    # For YouTube entries, also get the most recent video title
    domain_data = []
    for r in results:
        entry = {
            "domain": r.domain,
            "category": r.category,
            "total_seconds": r.total_seconds,
            "visit_count": r.visit_count,
        }

        # Get recent page titles for this domain (YouTube videos OR desktop windows)
        is_youtube = r.domain in ('youtube.com', 'youtu.be')
        is_desktop = r.domain.startswith('desktop://')

        if is_youtube or is_desktop:
            title_key = "videos" if is_youtube else "windows"
            title_query = db.query(
                TrackingLog.page_title,
                func.sum(TrackingLog.duration_seconds).label("seconds"),
            ).filter(
                TrackingLog.user_id == user_id,
                TrackingLog.domain == r.domain,
                TrackingLog.category == r.category,
                TrackingLog.page_title.isnot(None),
                TrackingLog.page_title != '',
            )

            if start:
                title_query = title_query.filter(TrackingLog.timestamp >= start)
            if end:
                title_query = title_query.filter(TrackingLog.timestamp <= end)

            title_query_results = title_query.group_by(TrackingLog.page_title).order_by(
                func.sum(TrackingLog.duration_seconds).desc()
            ).limit(5).all()

            entry[title_key] = [
                {"title": t.page_title, "seconds": t.seconds}
                for t in title_query_results
            ]

        domain_data.append(entry)

    return domain_data

