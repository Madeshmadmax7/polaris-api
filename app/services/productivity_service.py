"""
LifeOS – Productivity Service
Server-side productivity score calculation with fragmentation dampening.

FORMULA:
    FocusFactor = 1 / (1 + TabSwitchRate)
    ProductivityScore =
        Σ(Timeᵢ × Weightᵢ × FocusFactor)
        + (QuizAvg × K1)
        − log(Distractions + e) × K2

All scoring happens server-side to prevent spoofing.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import TrackingLog, DailySummary, QuizAttempt
from app.config.settings import settings


def calculate_focus_factor(
    productive_seconds: int,
    neutral_seconds: int,
    total_active_seconds: int,
) -> float:
    """
    FocusFactor = (productive + neutral) / total_active
    Range: [0, 1] — higher is better.
    Measures what fraction of browsing time was focused (non-distracting).
    """
    total = max(total_active_seconds, 1)
    focused = productive_seconds + neutral_seconds
    return min(focused / total, 1.0)


def calculate_productivity_score(
    productive_seconds: int,
    neutral_seconds: int,
    distracting_seconds: int,
    tab_switches: int,
    total_active_seconds: int,
    quiz_average: float = 0.0,
) -> float:
    """
    Productivity score: higher when more productive, lower when more distracting.
    Established Formula: ((productive + neutral*0.3 - distracting*0.5) / total) * 100
    Clamped to 0-100.
    """
    total = max(total_active_seconds, 1)

    # Weighted: productive=+1.0, neutral=+0.3, distracting=-0.5
    weighted = (
        productive_seconds * 1.0
        + neutral_seconds * 0.3
        - distracting_seconds * 0.5
    )

    raw = (weighted / total) * 100.0

    # Quiz bonus (up to +10 points)
    quiz_bonus = quiz_average * 0.1

    score = raw + quiz_bonus
    return round(max(0.0, min(100.0, score)), 2)


def compute_daily_summary(
    db: Session,
    user_id: str,
    date: datetime,
) -> DailySummary:
    """Compute and store/update the aggregated daily summary."""
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # Aggregate tracking logs for the day
    logs = db.query(TrackingLog).filter(
        TrackingLog.user_id == user_id,
        TrackingLog.timestamp >= day_start,
        TrackingLog.timestamp < day_end,
    ).all()

    productive_seconds = sum(l.duration_seconds for l in logs if l.category == "productive")
    neutral_seconds = sum(l.duration_seconds for l in logs if l.category == "neutral")
    distracting_seconds = sum(l.duration_seconds for l in logs if l.category == "distracting")
    total_active = sum(l.duration_seconds for l in logs)
    total_tabs = sum(l.tab_switches for l in logs)

    # Get quiz average for the day
    quiz_avg = db.query(func.avg(QuizAttempt.score)).filter(
        QuizAttempt.user_id == user_id,
        QuizAttempt.completed_at >= day_start,
        QuizAttempt.completed_at < day_end,
        QuizAttempt.score.isnot(None),
    ).scalar() or 0.0

    focus_factor = calculate_focus_factor(
        productive_seconds, neutral_seconds, total_active,
    )
    productivity_score = calculate_productivity_score(
        productive_seconds, neutral_seconds, distracting_seconds,
        total_tabs, total_active, float(quiz_avg),
    )

    # Domain breakdown for top domains
    domain_data = db.query(
        TrackingLog.domain,
        TrackingLog.category,
        func.sum(TrackingLog.duration_seconds).label("secs")
    ).filter(
        TrackingLog.user_id == user_id,
        TrackingLog.timestamp >= day_start,
        TrackingLog.timestamp < day_end,
    ).group_by(TrackingLog.domain, TrackingLog.category).order_by(
        func.sum(TrackingLog.duration_seconds).desc()
    ).limit(10).all()

    top_domains = [{"domain": d.domain, "category": d.category, "seconds": int(d.secs)} for d in domain_data]

    # Upsert daily summary
    existing = db.query(DailySummary).filter(
        DailySummary.user_id == user_id,
        DailySummary.date == day_start,
    ).first()

    if existing:
        existing.total_active_seconds = total_active
        existing.productive_seconds = productive_seconds
        existing.neutral_seconds = neutral_seconds
        existing.distracting_seconds = distracting_seconds
        existing.total_tab_switches = total_tabs
        existing.focus_factor = focus_factor
        existing.productivity_score = productivity_score
        existing.quiz_average = float(quiz_avg)
        existing.top_domains = top_domains
        db.commit()
        db.refresh(existing)
        return existing
    else:
        summary = DailySummary(
            user_id=user_id,
            date=day_start,
            total_active_seconds=total_active,
            productive_seconds=productive_seconds,
            neutral_seconds=neutral_seconds,
            distracting_seconds=distracting_seconds,
            total_tab_switches=total_tabs,
            focus_factor=focus_factor,
            productivity_score=productivity_score,
            quiz_average=float(quiz_avg),
            top_domains=top_domains,
        )
        db.add(summary)
        db.commit()
        db.refresh(summary)
        return summary


def get_productivity_trend(
    db: Session,
    user_id: str,
    days: int = 7,
) -> dict:
    """Get productivity trend over N days."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    summaries = db.query(DailySummary).filter(
        DailySummary.user_id == user_id,
        DailySummary.date >= start,
    ).order_by(DailySummary.date.asc()).all()

    scores = []
    for s in summaries:
        scores.append({
            "date": s.date.strftime("%Y-%m-%d"),
            "productivity_score": s.productivity_score,
            "focus_factor": s.focus_factor,
            "total_active_minutes": s.total_active_seconds / 60.0,
            "productive_minutes": s.productive_seconds / 60.0,
            "neutral_minutes": s.neutral_seconds / 60.0 if s.neutral_seconds else 0,
            "distracting_minutes": s.distracting_seconds / 60.0,
            "tab_switches": s.total_tab_switches,
            "quiz_average": s.quiz_average,
            "top_domains": s.top_domains or [],
        })

    avg_score = sum(s["productivity_score"] for s in scores) / max(len(scores), 1)

    # Determine trend
    if len(scores) >= 3:
        recent = sum(s["productivity_score"] for s in scores[-3:]) / 3
        older = sum(s["productivity_score"] for s in scores[:3]) / 3
        if recent > older * 1.1:
            trend = "improving"
        elif recent < older * 0.9:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "stable"

    return {
        "scores": scores,
        "average_score": round(avg_score, 2),
        "trend": trend,
    }
