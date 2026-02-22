"""
LifeOS – Productivity Routes
Server-side score retrieval and daily summary computation.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.models import User
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
    days: int = Query(7, ge=1, le=90),
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
