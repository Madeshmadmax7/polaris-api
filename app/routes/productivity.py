"""
LifeOS – Productivity Routes
Server-side score retrieval and daily summary computation.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from pydantic import BaseModel, Field
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


# ═══════════════════════════════════════════════════════════
#  STREAK SYSTEM
# ═══════════════════════════════════════════════════════════

@router.get("/streak")
def get_streak(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Compute learning streak from daily_summaries.
    A day counts if productive_seconds > 0 OR any chapter progress was made.
    Returns: current_streak, longest_streak, last_active_date, today_active.
    """
    from app.models.models import DailySummary
    # Fetch all days that had any productive seconds (last 2 years)
    summaries = db.query(DailySummary).filter(
        DailySummary.user_id == user.id,
        DailySummary.productive_seconds > 0,
    ).order_by(DailySummary.date.desc()).all()

    # Also count days with chapter completion even if summary not computed
    from app.models.models import ChapterProgress
    from sqlalchemy import func as sqlfunc
    chapter_days = db.query(
        sqlfunc.date(ChapterProgress.completed_at).label("day")
    ).filter(
        ChapterProgress.user_id == user.id,
        ChapterProgress.is_completed == True,
        ChapterProgress.completed_at.isnot(None),
    ).distinct().all()

    active_dates = set()
    for s in summaries:
        d = s.date.date() if hasattr(s.date, 'date') else s.date
        active_dates.add(str(d))
    for row in chapter_days:
        if row.day:
            active_dates.add(str(row.day))

    sorted_dates = sorted(active_dates, reverse=True)

    today = datetime.now(timezone.utc).date()
    today_str = str(today)
    yesterday_str = str(today - timedelta(days=1))

    # Current streak: consecutive days ending today or yesterday
    current_streak = 0
    if sorted_dates:
        # Start from today going backwards
        check = today
        for _ in range(len(sorted_dates) + 1):
            check_str = str(check)
            if check_str in active_dates:
                current_streak += 1
                check -= timedelta(days=1)
            else:
                # Allow one gap today (streak still active if last active was yesterday)
                if current_streak == 0 and check_str == today_str:
                    check -= timedelta(days=1)
                    if str(check) in active_dates:
                        current_streak += 1
                        check -= timedelta(days=1)
                        continue
                break

    # Longest streak ever
    longest_streak = 0
    cur = 0
    prev_date = None
    for d_str in sorted(active_dates):
        from datetime import date
        d = date.fromisoformat(d_str)
        if prev_date is None:
            cur = 1
        else:
            diff = (d - prev_date).days
            cur = cur + 1 if diff == 1 else 1
        longest_streak = max(longest_streak, cur)
        prev_date = d

    today_active = today_str in active_dates

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "total_active_days": len(active_dates),
        "today_active": today_active,
        "last_active_date": sorted_dates[0] if sorted_dates else None,
    }


# ═══════════════════════════════════════════════════════════
#  WEEKLY LEARNING REPORT
# ═══════════════════════════════════════════════════════════

@router.get("/weekly-report")
def get_weekly_report(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    In-app weekly learning report.
    Covers the last 7 days vs the 7 days before that.
    """
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_week_start = (now - timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0)

    # This week tracking time
    this_week_logs = db.query(TrackingLog).filter(
        TrackingLog.user_id == user.id,
        TrackingLog.timestamp >= week_start,
    ).all()
    prev_week_logs = db.query(TrackingLog).filter(
        TrackingLog.user_id == user.id,
        TrackingLog.timestamp >= prev_week_start,
        TrackingLog.timestamp < week_start,
    ).all()

    def summarize_logs(logs):
        total = sum(l.duration_seconds for l in logs)
        productive = sum(l.duration_seconds for l in logs if l.category == "productive")
        distracting = sum(l.duration_seconds for l in logs if l.category == "distracting")
        return total, productive, distracting

    this_total, this_productive, this_distracting = summarize_logs(this_week_logs)
    prev_total, prev_productive, prev_distracting = summarize_logs(prev_week_logs)

    # Chapter completions this week
    this_week_chapters = db.query(ChapterProgress).filter(
        ChapterProgress.user_id == user.id,
        ChapterProgress.is_completed == True,
        ChapterProgress.completed_at >= week_start,
    ).all()
    prev_week_chapters = db.query(ChapterProgress).filter(
        ChapterProgress.user_id == user.id,
        ChapterProgress.is_completed == True,
        ChapterProgress.completed_at >= prev_week_start,
        ChapterProgress.completed_at < week_start,
    ).all()

    # Quiz scores this week
    from app.models.models import QuizAttempt
    this_quiz = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user.id,
        QuizAttempt.completed_at >= week_start,
    ).all()
    prev_quiz = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user.id,
        QuizAttempt.completed_at >= prev_week_start,
        QuizAttempt.completed_at < week_start,
    ).all()

    this_quiz_avg = sum(q.score for q in this_quiz) / len(this_quiz) if this_quiz else None
    prev_quiz_avg = sum(q.score for q in prev_quiz) / len(prev_quiz) if prev_quiz else None

    # Top topics studied this week
    topic_seconds: dict = {}
    for ch in this_week_chapters:
        title = ch.chapter_title or "Unknown"
        topic_seconds[title] = topic_seconds.get(title, 0) + (ch.watched_seconds or 0)
    top_topics = sorted(topic_seconds.items(), key=lambda x: x[1], reverse=True)[:5]

    # Active days this week
    active_days = set(
        str(l.timestamp.date()) for l in this_week_logs if l.duration_seconds > 0
    )

    def pct_change(curr, prev):
        if prev == 0:
            return None
        return round(((curr - prev) / prev) * 100, 1)

    return {
        "period": {
            "start": week_start.isoformat(),
            "end": now.isoformat(),
        },
        "this_week": {
            "total_minutes": round(this_total / 60, 1),
            "productive_minutes": round(this_productive / 60, 1),
            "distracting_minutes": round(this_distracting / 60, 1),
            "chapters_completed": len(this_week_chapters),
            "quiz_average": round(this_quiz_avg, 1) if this_quiz_avg is not None else None,
            "active_days": len(active_days),
            "top_topics": [{"title": t, "seconds": s} for t, s in top_topics],
        },
        "vs_last_week": {
            "productive_minutes_change": pct_change(this_productive, prev_productive),
            "chapters_change": pct_change(len(this_week_chapters), len(prev_week_chapters)),
            "quiz_change": pct_change(this_quiz_avg or 0, prev_quiz_avg or 0) if this_quiz_avg and prev_quiz_avg else None,
        },
        "prev_week": {
            "productive_minutes": round(prev_productive / 60, 1),
            "chapters_completed": len(prev_week_chapters),
            "quiz_average": round(prev_quiz_avg, 1) if prev_quiz_avg is not None else None,
        },
    }


# ═══════════════════════════════════════════════════════════
#  LEARNING VELOCITY GRAPH
# ═══════════════════════════════════════════════════════════

@router.get("/learning-velocity")
def get_learning_velocity(
    days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns chapters completed per day for the last N days.
    Used for learning velocity line graph.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    completed = db.query(ChapterProgress).filter(
        ChapterProgress.user_id == user.id,
        ChapterProgress.is_completed == True,
        ChapterProgress.completed_at >= since,
    ).all()

    # Bucket by date
    from collections import defaultdict
    daily: dict = defaultdict(int)
    for ch in completed:
        if ch.completed_at:
            d = ch.completed_at.date()
            daily[str(d)] += 1

    # Build full date range with 0s for missing days
    start_date = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()
    end_date = datetime.now(timezone.utc).date()
    result = []
    current = start_date
    while current <= end_date:
        result.append({
            "date": str(current),
            "chapters": daily.get(str(current), 0),
        })
        current += timedelta(days=1)

    # Rolling 7-day average
    for i, point in enumerate(result):
        window = result[max(0, i - 6):i + 1]
        point["rolling_avg"] = round(sum(p["chapters"] for p in window) / len(window), 2)

    total = sum(p["chapters"] for p in result)
    peak = max(p["chapters"] for p in result) if result else 0

    return {
        "days": result,
        "total_completed": total,
        "peak_day": peak,
        "avg_per_day": round(total / days, 2),
    }


# ═══════════════════════════════════════════════════════════
#  TOPIC COVERAGE HEATMAP
# ═══════════════════════════════════════════════════════════

@router.get("/topic-heatmap")
def get_topic_heatmap(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns topic-level watch time breakdown across all plans.
    Groups by plan and shows per-chapter watch time.
    Used for topic coverage heatmap/bars in Analytics.
    """
    plans = db.query(StudyPlan).filter(StudyPlan.user_id == user.id).all()
    result = []

    for plan in plans:
        chapters = db.query(ChapterProgress).filter(
            ChapterProgress.study_plan_id == plan.id,
            ChapterProgress.user_id == user.id,
        ).order_by(ChapterProgress.chapter_index).all()

        if not chapters:
            continue

        max_watch = max((c.watched_seconds or 0) for c in chapters) or 1
        plan_total = sum(c.watched_seconds or 0 for c in chapters)

        topics = []
        for ch in chapters:
            watched = ch.watched_seconds or 0
            duration = ch.video_duration_seconds or 0
            topics.append({
                "chapter": ch.chapter_index,
                "title": ch.chapter_title,
                "watched_seconds": watched,
                "duration_seconds": duration,
                "watch_pct": round((watched / duration * 100) if duration > 0 else 0, 1),
                "relative_intensity": round(watched / max_watch, 3),
                "is_completed": ch.is_completed,
                "playback_rate": ch.playback_rate or 1.0,
            })

        result.append({
            "plan_id": plan.id,
            "plan_title": plan.title,
            "total_watch_seconds": plan_total,
            "total_watch_formatted": _format_seconds(plan_total),
            "topics": topics,
        })

    return {"plans": result, "total_plans": len(result)}


# ═══════════════════════════════════════════════════════════
#  FOCUS SESSION & ACTIVE FOCUS BLOCKING CONTROL
# ═══════════════════════════════════════════════════════════

class FocusSessionRequest(BaseModel):
    duration_minutes: int = Field(default=25, ge=1, le=180)
    preset_label: Optional[str] = "25 min"


@router.post("/focus-session/start")
async def start_focus_session(
    payload: FocusSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Start an active Focus Session:
    1. Triggers real-time WebSocket event to active Chrome Extensions & Desktop Trackers.
    2. Enforces blocking on distracting domains (Instagram, Reddit, TikTok, Facebook, Twitter, Shorts).
    3. Keeps YouTube educational watching UNBLOCKED for study & learning!
    """
    distracting_domains = [
        "instagram.com", "facebook.com", "twitter.com", "x.com",
        "tiktok.com", "reddit.com", "twitch.tv", "netflix.com",
        "youtube.com/shorts"  # Only block distracting Shorts, keep main YouTube unblocked for learning
    ]

    # Save focus mode to DB
    user.focus_mode_until = datetime.now(timezone.utc) + timedelta(minutes=payload.duration_minutes)
    db.commit()

    from app.services.parental_service import get_blocked_sites
    user_blocked = get_blocked_sites(db, user.id)

    # Filter out main youtube.com if present, as user requested YouTube for learning!
    filtered_blocked = [d for d in user_blocked if d.lower() != "youtube.com"]
    combined_domains = list(set(distracting_domains + filtered_blocked))

    from app.websocket.manager import ws_manager
    await ws_manager.send_to_user(user.id, {
        "type": "focus_session_start",
        "data": {
            "duration_minutes": payload.duration_minutes,
            "preset_label": payload.preset_label,
            "blocked_domains": combined_domains,
            "youtube_allowed_for_learning": True,
        }
    })

    return {
        "status": "active",
        "duration_minutes": payload.duration_minutes,
        "blocked_domains": combined_domains,
        "youtube_learning_mode": True,
    }


@router.post("/focus-session/stop")
async def stop_focus_session(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stop active Focus Session and restore default site access."""
    # Clear focus mode from DB
    user.focus_mode_until = None
    db.commit()

    from app.websocket.manager import ws_manager
    from app.services.parental_service import get_blocked_sites

    user_blocked = get_blocked_sites(db, user.id)
    await ws_manager.send_to_user(user.id, {
        "type": "focus_session_stop",
        "data": {
            "default_blocked": user_blocked,
        }
    })

    return {"status": "stopped", "restored_domains": user_blocked}


