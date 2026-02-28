"""
LifeOS – Productivity Routes
Server-side score retrieval and daily summary computation.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config.database import get_db
from app.models.models import User, TrackingLog, ChapterProgress, StudyPlan
from app.utils.auth import get_current_user
from app.services.productivity_service import (
    compute_daily_summary,
    get_productivity_trend,
)

router = APIRouter(prefix="/productivity", tags=["Productivity"])


@router.get("/today")
def get_today_score(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get today's productivity score (recomputed on request)."""
    today = datetime.now(timezone.utc)
    summary = compute_daily_summary(db, user.id, today)
    return {
        "date": summary.date.strftime("%Y-%m-%d"),
        "productivity_score": summary.productivity_score,
        "focus_factor": summary.focus_factor,
        "total_active_minutes": summary.total_active_seconds / 60.0,
        "productive_minutes": summary.productive_seconds / 60.0,
        "neutral_minutes": summary.neutral_seconds / 60.0,
        "distracting_minutes": summary.distracting_seconds / 60.0,
        "tab_switches": summary.total_tab_switches,
        "quiz_average": summary.quiz_average,
        "top_domains": summary.top_domains or [],
    }


@router.get("/trend")
def get_trend(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get productivity trend over N days."""
    return get_productivity_trend(db, user.id, days)


@router.post("/recompute")
def recompute_summary(
    date: str = Query(..., description="ISO date to recompute"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Force recomputation of daily summary for a specific date."""
    dt = datetime.fromisoformat(date)
    summary = compute_daily_summary(db, user.id, dt)
    return {"status": "recomputed", "score": summary.productivity_score}


# ═══════════════════════════════════════════════════════════
#  DASHBOARD STATS (Full breakdown with seconds & percentages)
# ═══════════════════════════════════════════════════════════

@router.get("/dashboard-stats")
def get_dashboard_stats(
    start: Optional[str] = Query(None, description="ISO date string"),
    end: Optional[str] = Query(None, description="ISO date string"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Comprehensive dashboard stats with per-site seconds, percentages,
    YouTube video breakdown, ChatGPT tracking, and course progress.
    """
    # Default to today
    if start:
        start_dt = datetime.fromisoformat(start).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    if end:
        end_dt = datetime.fromisoformat(end)
    else:
        end_dt = start_dt + timedelta(days=1)

    # ── Per-domain time breakdown ──
    domain_query = db.query(
        TrackingLog.domain,
        TrackingLog.category,
        func.sum(TrackingLog.duration_seconds).label("total_seconds"),
        func.count(TrackingLog.id).label("visit_count"),
    ).filter(
        TrackingLog.user_id == user.id,
        TrackingLog.timestamp >= start_dt,
        TrackingLog.timestamp < end_dt,
    ).group_by(TrackingLog.domain, TrackingLog.category).order_by(
        func.sum(TrackingLog.duration_seconds).desc()
    ).all()

    # Total active seconds
    total_seconds = sum(r.total_seconds for r in domain_query)
    productive_seconds = sum(r.total_seconds for r in domain_query if r.category == 'productive')
    neutral_seconds = sum(r.total_seconds for r in domain_query if r.category == 'neutral')
    distracting_seconds = sum(r.total_seconds for r in domain_query if r.category == 'distracting')

    # ── Build per-site breakdown with percentages ──
    sites = []
    for r in domain_query:
        pct = (r.total_seconds / total_seconds * 100) if total_seconds > 0 else 0
        entry = {
            "domain": r.domain,
            "category": r.category,
            "total_seconds": r.total_seconds,
            "formatted_time": _format_seconds(r.total_seconds),
            "percentage": round(pct, 1),
            "visit_count": r.visit_count,
        }

        # YouTube videos breakdown
        if r.domain in ('youtube.com', 'youtu.be'):
            video_query = db.query(
                TrackingLog.page_title,
                TrackingLog.category,
                func.sum(TrackingLog.duration_seconds).label("seconds"),
                func.count(TrackingLog.id).label("count"),
            ).filter(
                TrackingLog.user_id == user.id,
                TrackingLog.domain == r.domain,
                TrackingLog.timestamp >= start_dt,
                TrackingLog.timestamp < end_dt,
                TrackingLog.page_title.isnot(None),
                TrackingLog.page_title != '',
            ).group_by(TrackingLog.page_title, TrackingLog.category).order_by(
                func.sum(TrackingLog.duration_seconds).desc()
            ).limit(20).all()

            entry["videos"] = [
                {
                    "title": v.page_title,
                    "category": v.category,
                    "seconds": v.seconds,
                    "formatted_time": _format_seconds(v.seconds),
                    "percentage": round((v.seconds / total_seconds * 100) if total_seconds > 0 else 0, 1),
                    "view_count": v.count,
                }
                for v in video_query
            ]

        # ChatGPT / AI tools page title breakdown
        if r.domain in ('chatgpt.com', 'chat.openai.com', 'claude.ai', 'bard.google.com', 'gemini.google.com', 'copilot.microsoft.com'):
            page_query = db.query(
                TrackingLog.page_title,
                func.sum(TrackingLog.duration_seconds).label("seconds"),
                func.count(TrackingLog.id).label("count"),
            ).filter(
                TrackingLog.user_id == user.id,
                TrackingLog.domain == r.domain,
                TrackingLog.timestamp >= start_dt,
                TrackingLog.timestamp < end_dt,
                TrackingLog.page_title.isnot(None),
                TrackingLog.page_title != '',
            ).group_by(TrackingLog.page_title).order_by(
                func.sum(TrackingLog.duration_seconds).desc()
            ).limit(10).all()

            entry["sessions"] = [
                {
                    "title": p.page_title,
                    "seconds": p.seconds,
                    "formatted_time": _format_seconds(p.seconds),
                    "percentage": round((p.seconds / total_seconds * 100) if total_seconds > 0 else 0, 1),
                    "count": p.count,
                }
                for p in page_query
            ]

        sites.append(entry)

    # ── Category summary ──
    category_summary = {
        "productive": {
            "seconds": productive_seconds,
            "formatted_time": _format_seconds(productive_seconds),
            "percentage": round((productive_seconds / total_seconds * 100) if total_seconds > 0 else 0, 1),
        },
        "neutral": {
            "seconds": neutral_seconds,
            "formatted_time": _format_seconds(neutral_seconds),
            "percentage": round((neutral_seconds / total_seconds * 100) if total_seconds > 0 else 0, 1),
        },
        "distracting": {
            "seconds": distracting_seconds,
            "formatted_time": _format_seconds(distracting_seconds),
            "percentage": round((distracting_seconds / total_seconds * 100) if total_seconds > 0 else 0, 1),
        },
    }

    # ── Course progress ──
    plans = db.query(StudyPlan).filter(StudyPlan.user_id == user.id).all()
    course_progress = []
    total_course_chapters = 0
    total_course_completed = 0
    total_watch_seconds = 0
    total_video_duration = 0

    for plan in plans:
        chapters = db.query(ChapterProgress).filter(
            ChapterProgress.study_plan_id == plan.id,
            ChapterProgress.user_id == user.id
        ).order_by(ChapterProgress.chapter_index).all()

        completed = sum(1 for c in chapters if c.is_completed)
        total = len(chapters)
        plan_watched = sum(c.watched_seconds or 0 for c in chapters)
        plan_duration = sum(c.video_duration_seconds or 0 for c in chapters)
        plan_pct = (completed / total * 100) if total > 0 else 0
        watch_pct = (plan_watched / plan_duration * 100) if plan_duration > 0 else 0

        total_course_chapters += total
        total_course_completed += completed
        total_watch_seconds += plan_watched
        total_video_duration += plan_duration

        course_progress.append({
            "plan_id": plan.id,
            "title": plan.title,
            "goal": plan.goal,
            "total_chapters": total,
            "completed_chapters": completed,
            "completion_percentage": round(plan_pct, 1),
            "total_watched_seconds": plan_watched,
            "total_video_duration_seconds": plan_duration,
            "watch_percentage": round(watch_pct, 1),
            "formatted_watched": _format_seconds(plan_watched),
            "formatted_duration": _format_seconds(plan_duration),
            "quiz_unlocked": plan.quiz_unlocked,
            "chapters": [
                {
                    "chapter_index": c.chapter_index,
                    "title": c.chapter_title,
                    "is_completed": c.is_completed,
                    "watched_seconds": c.watched_seconds or 0,
                    "video_duration_seconds": c.video_duration_seconds or 0,
                    "watch_percentage": round(
                        ((c.watched_seconds or 0) / c.video_duration_seconds * 100)
                        if c.video_duration_seconds and c.video_duration_seconds > 0 else 0, 1
                    ),
                    "formatted_watched": _format_seconds(c.watched_seconds or 0),
                    "formatted_duration": _format_seconds(c.video_duration_seconds or 0),
                    "creator_name": c.creator_name,
                }
                for c in chapters
            ],
        })

    overall_course_pct = (total_course_completed / total_course_chapters * 100) if total_course_chapters > 0 else 0
    overall_watch_pct = (total_watch_seconds / total_video_duration * 100) if total_video_duration > 0 else 0

    return {
        "total_seconds": total_seconds,
        "formatted_total_time": _format_seconds(total_seconds),
        "category_summary": category_summary,
        "sites": sites,
        "course_progress": {
            "total_plans": len(plans),
            "total_chapters": total_course_chapters,
            "completed_chapters": total_course_completed,
            "overall_completion_percentage": round(overall_course_pct, 1),
            "total_watched_seconds": total_watch_seconds,
            "total_video_duration_seconds": total_video_duration,
            "overall_watch_percentage": round(overall_watch_pct, 1),
            "formatted_watched": _format_seconds(total_watch_seconds),
            "formatted_duration": _format_seconds(total_video_duration),
            "plans": course_progress,
        },
    }


def _format_seconds(seconds: int) -> str:
    """Format seconds to human-readable: 1h 23m 45s or 5m 30s or 45s."""
    if seconds < 0:
        seconds = 0
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"
