"""
Parental Connection Routes
OTP-based parent-child connection system for analytics access.
"""

import os
import shutil
import tempfile
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.config.database import get_db
from app.models.models import (
    User,
    ParentChildConnection,
    Document,
    StudyPlan,
    ChapterProgress,
    QuizAttempt,
    Notification,
)
from app.utils.auth import get_current_user
from app.services.parental_connection_service import (
    create_connection_request,
    get_connection_request,
    verify_connection,
    check_connection_validity,
    get_active_connections,
    disconnect_connection
)
from app.services.productivity_service import get_productivity_trend, compute_daily_summary
from app.services.rag_service import process_document
from app.services.ai_service import generate_study_plan_with_quiz, get_learner_profile_context
from pydantic import BaseModel


router = APIRouter(prefix="/parental", tags=["parental-connection"])


OTP_VALIDITY_SECONDS = 120


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ═══════════════════════════════════════════════════════════
#  REQUEST SCHEMAS
# ═══════════════════════════════════════════════════════════

class RequestConnectionInput(BaseModel):
    child_email: str


class VerifyConnectionInput(BaseModel):
    connection_id: str
    otp_code: str


class VerifyConnectionByEmailInput(BaseModel):
    child_email: str


class ParentStudyPlanInput(BaseModel):
    goal: str
    duration_days: int
    document_id: str | None = None


def _ensure_parent_child_access(db: Session, parent_id: str, child_id: str) -> User:
    """Validate parent role + active OTP connection and return child user."""
    parent = db.query(User).filter(User.id == parent_id).first()
    if not parent or parent.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can access this endpoint")

    if not check_connection_validity(db, parent_id, child_id):
        raise HTTPException(status_code=401, detail="No active connection or connection has expired")

    child = db.query(User).filter(User.id == child_id, User.role == "student").first()
    if not child:
        raise HTTPException(status_code=404, detail="Child user not found")
    return child


def _format_seconds(seconds: int) -> str:
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
    otp_code: str


# ═══════════════════════════════════════════════════════════
#  STEP 1: PARENT SENDS CONNECTION REQUEST
# ═══════════════════════════════════════════════════════════

@router.post("/request-connection")
def request_connection(
    payload: RequestConnectionInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Parent initiates connection to child by email.
    Generates OTP and sends notification to child.
    """
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can request connections")
    
    try:
        result = create_connection_request(db, user.id, payload.child_email)
        return {
            "success": True,
            "connection_id": result["connection_id"],
            "message": result["message"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ═══════════════════════════════════════════════════════════
#  STEP 2: CHILD RECEIVES AND VIEWS OTP
# ═══════════════════════════════════════════════════════════

@router.get("/connection-request/{connection_id}")
def get_connection_details(
    connection_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Child views connection request and OTP code.
    """
    try:
        result = get_connection_request(db, connection_id, user.id)
        return {
            "success": True,
            "parent_name": result["parent_name"],
            "otp_code": result["otp_code"],
            "expires_in_seconds": result["expires_in_seconds"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ═══════════════════════════════════════════════════════════
#  STEP 3: PARENT ENTERS OTP FOR VERIFICATION
# ═══════════════════════════════════════════════════════════

@router.post("/verify-connection")
def verify_otp(
    payload: VerifyConnectionInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Parent verifies OTP code to activate connection.
    Connection becomes active for 30 days after verification.
    """
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can verify connections")
    
    try:
        result = verify_connection(db, payload.connection_id, user.id, payload.otp_code)
        return {
            "success": True,
            "verified": True,
            "child_id": result["child_id"],
            "child_name": result["child_name"],
            "expires_at": result["expires_at"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify-connection-by-email")
def verify_otp_by_email(
    payload: VerifyConnectionByEmailInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Parent verifies OTP using child's email.
    This is a fallback UX path when pending card is not visible.
    """
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can verify connections")

    child = db.query(User).filter(User.email == payload.child_email).first()
    if not child:
        raise HTTPException(status_code=404, detail="Child user not found")

    now = datetime.now(timezone.utc)
    pending = db.query(ParentChildConnection).filter(
        ParentChildConnection.parent_id == user.id,
        ParentChildConnection.child_id == child.id,
        ParentChildConnection.status == "pending"
    ).order_by(ParentChildConnection.otp_created_at.desc()).first()

    if not pending:
        raise HTTPException(status_code=400, detail="No pending OTP request found for this child")

    # Auto-expire stale OTPs before verification.
    otp_created_at = _as_utc(pending.otp_created_at)
    if otp_created_at and (now - otp_created_at) > timedelta(seconds=OTP_VALIDITY_SECONDS):
        pending.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="OTP expired. Please request a new connection")

    try:
        result = verify_connection(db, pending.id, user.id, payload.otp_code)
        return {
            "success": True,
            "verified": True,
            "child_id": result["child_id"],
            "child_name": result["child_name"],
            "expires_at": result["expires_at"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ═══════════════════════════════════════════════════════════
#  STEP 4: PARENT VIEWS CHILD ANALYTICS
# ═══════════════════════════════════════════════════════════

@router.get("/child-dashboard/{child_id}")
def get_child_dashboard(
    child_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Parent views child's productivity analytics.
    Only accessible if connection is active and not expired.
    Reuses existing productivity analytics logic.
    """
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can view child dashboards")
    
    #Check connection validity
    if not check_connection_validity(db, user.id, child_id):
        raise HTTPException(
            status_code=401,
            detail="No active connection or connection has expired"
        )
    
    # Get child user
    child = db.query(User).filter(User.id == child_id).first()
    if not child:
        raise HTTPException(status_code=404, detail="Child user not found")
    
    # Reuse productivity_service functions - no duplication
    # Get recent trend
    trend = get_productivity_trend(db, child_id, days=14)
    
    # Get today's summary
    today = datetime.now(timezone.utc)
    today_summary = compute_daily_summary(db, child_id, today)
    
    return {
        "success": True,
        "child_id": child_id,
        "child_name": child.username,
        "trend": trend,
        "today": today_summary
    }


@router.get("/child/{child_id}/today")
def get_child_today_score(
    child_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent-safe proxy for child's today score (same shape as /productivity/today)."""
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can view child dashboards")

    if not check_connection_validity(db, user.id, child_id):
        raise HTTPException(status_code=401, detail="No active connection or connection has expired")

    today = datetime.now(timezone.utc)
    summary = compute_daily_summary(db, child_id, today)
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


@router.get("/child/{child_id}/trend")
def get_child_trend(
    child_id: str,
    days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent-safe proxy for child's trend (same shape as /productivity/trend)."""
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can view child dashboards")

    if not check_connection_validity(db, user.id, child_id):
        raise HTTPException(status_code=401, detail="No active connection or connection has expired")

    return get_productivity_trend(db, child_id, days)


@router.get("/child/{child_id}/dashboard-stats")
def get_child_dashboard_stats(
    child_id: str,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent-safe proxy for child's dashboard stats (same shape as /productivity/dashboard-stats)."""
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can view child dashboards")

    if not check_connection_validity(db, user.id, child_id):
        raise HTTPException(status_code=401, detail="No active connection or connection has expired")

    from app.models.models import TrackingLog, ChapterProgress, StudyPlan

    if start:
        start_dt = datetime.fromisoformat(start).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    if end:
        end_dt = datetime.fromisoformat(end)
    else:
        end_dt = start_dt + timedelta(days=1)

    domain_query = db.query(
        TrackingLog.domain,
        TrackingLog.category,
        func.sum(TrackingLog.duration_seconds).label("total_seconds"),
        func.count(TrackingLog.id).label("visit_count"),
    ).filter(
        TrackingLog.user_id == child_id,
        TrackingLog.timestamp >= start_dt,
        TrackingLog.timestamp < end_dt,
    ).group_by(TrackingLog.domain, TrackingLog.category).order_by(
        func.sum(TrackingLog.duration_seconds).desc()
    ).all()

    total_seconds = sum(r.total_seconds for r in domain_query)
    productive_seconds = sum(r.total_seconds for r in domain_query if r.category == 'productive')
    neutral_seconds = sum(r.total_seconds for r in domain_query if r.category == 'neutral')
    distracting_seconds = sum(r.total_seconds for r in domain_query if r.category == 'distracting')

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

        if r.domain in ('youtube.com', 'youtu.be'):
            video_query = db.query(
                TrackingLog.page_title,
                TrackingLog.category,
                func.sum(TrackingLog.duration_seconds).label("seconds"),
                func.count(TrackingLog.id).label("count"),
            ).filter(
                TrackingLog.user_id == child_id,
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

        if r.domain in ('chatgpt.com', 'chat.openai.com', 'claude.ai', 'bard.google.com', 'gemini.google.com', 'copilot.microsoft.com'):
            page_query = db.query(
                TrackingLog.page_title,
                func.sum(TrackingLog.duration_seconds).label("seconds"),
                func.count(TrackingLog.id).label("count"),
            ).filter(
                TrackingLog.user_id == child_id,
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

    plans = db.query(StudyPlan).filter(StudyPlan.user_id == child_id).all()
    course_progress = []
    total_course_chapters = 0
    total_course_completed = 0
    total_watch_seconds = 0
    total_video_duration = 0

    for plan in plans:
        chapters = db.query(ChapterProgress).filter(
            ChapterProgress.study_plan_id == plan.id,
            ChapterProgress.user_id == child_id
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


# ═══════════════════════════════════════════════════════════
#  ACTIVE CONNECTIONS MANAGEMENT
# ═══════════════════════════════════════════════════════════

@router.get("/my-connections")
def get_my_connections(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get all active connections for current user.
    Returns:
    - Children (if parent)
    - Parents (if child)
    """
    as_parent = user.role == "parent"
    connections_raw = get_active_connections(db, user.id, as_parent=as_parent)
    
    # Format for frontend
    connections = []
    for conn in connections_raw:
        conn_formatted = {
            "id": conn["connection_id"],
            "connection_id": conn["connection_id"],
            "user_id": conn["user_id"],
            "username": conn["username"],
            "email": conn["email"],
            "child_username": conn["username"] if as_parent else None,
            "child_email": conn["email"] if as_parent else None,
            "parent_username": conn["username"] if not as_parent else None,
            "parent_email": conn["email"] if not as_parent else None,
            "role": conn["role"],
            "connected_at": conn["connected_at"],
            "expires_at": conn["expires_at"],
            "expires_in_days": conn["expires_in_days"]
        }
        connections.append(conn_formatted)
    
    return {
        "success": True,
        "role": user.role,
        "connections": connections
    }


@router.get("/pending-requests")
def get_pending_requests(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get all pending connection requests initiated by parent.
    """
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can view pending requests")
    
    now = datetime.now(timezone.utc)
    pending = db.query(ParentChildConnection).filter(
        ParentChildConnection.parent_id == user.id,
        ParentChildConnection.status == "pending",
        ParentChildConnection.otp_created_at > now - timedelta(seconds=OTP_VALIDITY_SECONDS)
    ).all()
    
    result = []
    for conn in pending:
        child = db.query(User).filter(User.id == conn.child_id).first()
        otp_created_at = _as_utc(conn.otp_created_at)
        age = now - otp_created_at
        expires_in = max(0, OTP_VALIDITY_SECONDS - int(age.total_seconds()))
        
        result.append({
            "connection_id": conn.id,
            "child_id": conn.child_id,
            "child_name": child.username if child else "Unknown",
            "child_email": child.email if child else None,
            "otp_created_at": otp_created_at.isoformat(),
            "expires_in_seconds": expires_in
        })
    
    return {"success": True, "pending_requests": result}


@router.post("/cancel-pending/{connection_id}")
def cancel_pending(
    connection_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Cancel a pending connection request.
    Only parent who initiated can cancel.
    """
    connection = db.query(ParentChildConnection).filter(
        ParentChildConnection.id == connection_id,
        ParentChildConnection.parent_id == user.id,
        ParentChildConnection.status == "pending"
    ).first()
    
    if not connection:
        raise HTTPException(status_code=404, detail="Pending connection not found")
    
    # Delete the pending connection
    db.delete(connection)
    db.commit()
    
    return {"success": True, "message": "Connection request cancelled"}


@router.post("/disconnect/{connection_id}")
def disconnect(
    connection_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Disconnect a parent-child connection.
    Either parent or child can initiate.
    """
    try:
        result = disconnect_connection(db, connection_id, user.id)
        return {
            "success": True,
            "message": result["message"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ═══════════════════════════════════════════════════════════
#  PARENT COURSE GENERATOR (FOR LINKED CHILD)
# ═══════════════════════════════════════════════════════════

@router.post("/child/{child_id}/upload-document")
async def upload_child_document(
    child_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent uploads study material directly to linked child's learning library."""
    _ensure_parent_child_access(db, user.id, child_id)

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        doc = await asyncio.to_thread(process_document, db, child_id, tmp_path, file.filename)
        return {
            "success": True,
            "document": {
                "id": doc.id,
                "filename": doc.filename,
                "file_type": doc.file_type,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/child/{child_id}/documents")
def list_child_documents(
    child_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent lists linked child's uploaded study materials."""
    _ensure_parent_child_access(db, user.id, child_id)

    docs = db.query(Document).filter(Document.user_id == child_id).order_by(Document.created_at.desc()).all()
    return {
        "success": True,
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "file_type": d.file_type,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ],
    }


@router.post("/child/{child_id}/study-plan")
async def create_child_study_plan(
    child_id: str,
    payload: ParentStudyPlanInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent generates a study plan for linked child and sends enrollment notification."""
    child = _ensure_parent_child_access(db, user.id, child_id)

    if payload.duration_days < 1 or payload.duration_days > 365:
        raise HTTPException(status_code=400, detail="Duration days must be between 1 and 365")

    if payload.document_id:
        doc = db.query(Document).filter(
            Document.id == payload.document_id,
            Document.user_id == child_id,
        ).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found for this child")

    recent_attempts = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == child_id,
        QuizAttempt.study_plan_id.isnot(None),
    ).order_by(QuizAttempt.created_at.desc()).limit(5).all()

    difficulty = "medium"
    if recent_attempts:
        avg_score = sum(a.score for a in recent_attempts) / len(recent_attempts)
        if avg_score >= 80:
            difficulty = "hard"
        elif avg_score < 50:
            difficulty = "easy"

    learner_context = get_learner_profile_context(db, child_id)

    plan_data = await asyncio.to_thread(
        generate_study_plan_with_quiz,
        db,
        child_id,
        payload.goal,
        payload.duration_days,
        payload.document_id,
        difficulty,
        learner_context,
    )

    plan = StudyPlan(
        user_id=child_id,
        title=plan_data.get("title", payload.goal[:100]),
        goal=payload.goal,
        plan_data=plan_data,
        duration_days=payload.duration_days,
        document_id=payload.document_id,
        quiz_unlocked=False,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    chapters = plan_data.get("chapters", [])
    for chapter in chapters:
        db.add(ChapterProgress(
            study_plan_id=plan.id,
            user_id=child_id,
            chapter_index=chapter.get("chapter_number", 0),
            chapter_title=chapter.get("title", ""),
            youtube_url=chapter.get("youtube_url", ""),
            keyword_importance=chapter.get("keyword_importance", {}),
            is_completed=False,
        ))

    enrollment_notification = Notification(
        user_id=child_id,
        type="course_enrolled",
        title="New Course Assigned",
        message=f"You are enrolled into a new course: {plan.title}",
        data={
            "study_plan_id": plan.id,
            "title": plan.title,
            "goal": plan.goal,
            "assigned_by_parent_id": user.id,
            "assigned_by_parent_name": user.username,
        },
    )
    db.add(enrollment_notification)
    db.commit()

    return {
        "success": True,
        "study_plan": {
            "id": plan.id,
            "title": plan.title,
            "goal": plan.goal,
            "duration_days": plan.duration_days,
            "quiz_unlocked": plan.quiz_unlocked,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "plan_data": plan.plan_data,
        },
        "message": f"Course created and child notified: {child.username}",
    }


@router.get("/child/{child_id}/study-plans")
def list_child_study_plans(
    child_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent lists linked child's study plans."""
    _ensure_parent_child_access(db, user.id, child_id)

    plans = db.query(StudyPlan).filter(StudyPlan.user_id == child_id).order_by(StudyPlan.created_at.desc()).all()
    return {
        "success": True,
        "study_plans": [
            {
                "id": p.id,
                "title": p.title,
                "goal": p.goal,
                "duration_days": p.duration_days,
                "quiz_unlocked": p.quiz_unlocked,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "plan_data": p.plan_data,
            }
            for p in plans
        ],
    }


@router.get("/child/{child_id}/study-plan/{plan_id}/progress")
def get_child_study_plan_progress(
    child_id: str,
    plan_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Parent views linked child's chapter-level progress for a course."""
    _ensure_parent_child_access(db, user.id, child_id)

    plan = db.query(StudyPlan).filter(
        StudyPlan.id == plan_id,
        StudyPlan.user_id == child_id,
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Study plan not found")

    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == child_id,
    ).order_by(ChapterProgress.chapter_index).all()

    return {
        "chapters": [
            {
                "chapter_index": p.chapter_index,
                "chapter_title": p.chapter_title,
                "youtube_title": p.youtube_title,
                "youtube_url": p.youtube_url,
                "video_duration_seconds": p.video_duration_seconds,
                "watched_seconds": p.watched_seconds,
                "progress_percentage": min((p.watched_seconds / p.video_duration_seconds * 100), 100) if p.video_duration_seconds > 0 else 0,
                "creator_name": p.creator_name,
                "is_completed": p.is_completed,
                "completed_at": p.completed_at.isoformat() if p.completed_at else None,
            }
            for p in progress
        ],
        "total_chapters": len(progress),
        "completed_chapters": sum(1 for p in progress if p.is_completed),
        "quiz_unlocked": plan.quiz_unlocked,
    }
