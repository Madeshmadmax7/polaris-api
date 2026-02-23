"""
LifeOS – Tracking Routes
Activity log ingestion from extension (single + batch).
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.models import User
from app.schemas.schemas import (
    TrackingLogCreate, TrackingBatchCreate, TrackingLogResponse,
    DomainCategoryCreate, DomainCategoryResponse,
)
from app.utils.auth import get_current_user
from app.services.tracking_service import (
    ingest_tracking_log, ingest_batch, get_user_logs, get_domain_breakdown,
)
from app.services.productivity_service import compute_daily_summary
from app.models.models import DomainCategory

router = APIRouter(prefix="/tracking", tags=["Activity Tracking"])


@router.post("/log", response_model=TrackingLogResponse, status_code=201)
def log_activity(
    data: TrackingLogCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Log a single activity entry from extension."""
    log = ingest_tracking_log(db, user.id, data)
    
    # Check if this is a YouTube video and update chapter progress
    if log.domain in ('youtube.com', 'youtu.be') and log.page_title:
        background_tasks.add_task(_update_video_progress, db, user.id, log.page_title, log.duration_seconds)
    
    # Broadcast live tracking update to frontend via WebSocket
    background_tasks.add_task(
        _broadcast_tracking, user.id, log.domain, log.category,
        log.duration_seconds, log.page_title,
        log.timestamp.isoformat() if log.timestamp else None,
    )
    return TrackingLogResponse.model_validate(log)


async def _broadcast_tracking(user_id, domain, category, duration, title, ts):
    from app.websocket.manager import ws_manager
    await ws_manager.send_to_user(user_id, {
        "type": "live_tracking",
        "data": {
            "domain": domain,
            "category": category,
            "duration_seconds": duration,
            "page_title": title,
            "timestamp": ts,
        }
    })


def _update_video_progress(db: Session, user_id: str, video_title: str, duration_seconds: int):
    """Update chapter progress if watching a course video."""
    try:
        from app.models.models import ChapterProgress
        
        # Find matching chapter by video title
        chapters = db.query(ChapterProgress).filter(
            ChapterProgress.user_id == user_id,
            ChapterProgress.is_completed == False
        ).all()
        
        # Simple matching: check if chapter title is in video title or vice versa
        video_title_lower = video_title.lower()
        for chapter in chapters:
            chapter_title_lower = chapter.chapter_title.lower()
            
            # Match if chapter title keywords are in video title
            chapter_keywords = set(chapter_title_lower.split())
            video_keywords = set(video_title_lower.split())
            
            # If at least 2 keywords match, consider it the same video
            common_keywords = chapter_keywords & video_keywords
            if len(common_keywords) >= 2:
                # Update watched seconds (cumulative)
                chapter.watched_seconds += duration_seconds
                
                # Auto-complete if watched >= 90%
                if chapter.video_duration_seconds > 0:
                    watch_percentage = (chapter.watched_seconds / chapter.video_duration_seconds) * 100
                    if watch_percentage >= 90 and not chapter.is_completed:
                        chapter.is_completed = True
                        from datetime import datetime, timezone
                        chapter.completed_at = datetime.now(timezone.utc)
                
                db.commit()
                break
    except Exception as e:
        print(f"[Video Progress Update Error]: {str(e)}")
        # Don't fail the tracking log if video progress update fails
        pass


@router.post("/batch", status_code=200)
def batch_log_activity(
    data: TrackingBatchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Batch log activity entries from extension offline buffer.
    Returns count of successfully ingested logs.
    """
    count = ingest_batch(db, user.id, data.logs)

    # Trigger daily summary recomputation
    today = datetime.now(timezone.utc)
    try:
        compute_daily_summary(db, user.id, today)
    except Exception:
        pass  # Non-critical

    return {"ingested": count, "total": len(data.logs)}


@router.get("/logs", response_model=list[TrackingLogResponse])
def get_logs(
    start: Optional[str] = Query(None, description="ISO date string"),
    end: Optional[str] = Query(None, description="ISO date string"),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get tracking logs for the current user."""
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    logs = get_user_logs(db, user.id, start_dt, end_dt, limit)
    return [TrackingLogResponse.model_validate(l) for l in logs]


@router.get("/domains")
def get_domains(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get domain breakdown for the current user."""
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    return get_domain_breakdown(db, user.id, start_dt, end_dt)


@router.post("/categories", response_model=DomainCategoryResponse, status_code=201)
def set_domain_category(
    data: DomainCategoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set a custom domain category for the current user."""
    existing = db.query(DomainCategory).filter(
        DomainCategory.domain_pattern == data.domain_pattern,
        DomainCategory.user_id == user.id,
    ).first()

    if existing:
        existing.category = data.category.value
        db.commit()
        db.refresh(existing)
        return DomainCategoryResponse.model_validate(existing)

    cat = DomainCategory(
        domain_pattern=data.domain_pattern,
        category=data.category.value,
        user_id=user.id,
        is_global=False,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return DomainCategoryResponse.model_validate(cat)


@router.get("/debug")
def debug_recent_logs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Debug: show 10 most recent tracking logs with all fields."""
    from app.models.models import TrackingLog
    logs = db.query(TrackingLog).filter(
        TrackingLog.user_id == user.id,
    ).order_by(TrackingLog.timestamp.desc()).limit(10).all()
    return [{
        "id": l.id,
        "domain": l.domain,
        "category": l.category,
        "duration": l.duration_seconds,
        "page_title": l.page_title,
        "is_active": l.is_active,
        "timestamp": l.timestamp.isoformat() if l.timestamp else None,
    } for l in logs]

