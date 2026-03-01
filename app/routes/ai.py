"""
LifeOS – AI & Learning Routes (Unified Flow)
Document upload → Study plan with YouTube chapters → Chapter completion → Quiz
"""

import os
import shutil
import tempfile
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Body
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.models import User, StudyPlan, ChapterProgress, QuizAttempt
from app.schemas.schemas import (
    DocumentResponse,
    StudyPlanRequest, StudyPlanResponse,
    QuizSubmitRequest, QuizResponse,
)
from app.utils.auth import get_current_user
from app.services.rag_service import process_document
from app.services.ai_service import generate_study_plan_with_quiz
from app.services import matching_service as _matching

router = APIRouter(prefix="/ai", tags=["AI & Learning"])


# ═══════════════════════════════════════════════════════════
#  DOCUMENT UPLOAD
# ═══════════════════════════════════════════════════════════

@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload a syllabus/curriculum PDF.
    Fast extraction: PDF → text only (no embeddings/FAISS).
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        # Run blocking ML operations in thread pool to avoid freezing event loop
        doc = await asyncio.to_thread(process_document, db, user.id, tmp_path, file.filename)
        return DocumentResponse.model_validate(doc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/documents", response_model=list[DocumentResponse])
def list_documents(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all uploaded documents."""
    from app.models.models import Document
    docs = db.query(Document).filter(Document.user_id == user.id).order_by(
        Document.created_at.desc()
    ).all()
    return [DocumentResponse.model_validate(d) for d in docs]


# ═══════════════════════════════════════════════════════════
#  RAG QUERY
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
#  STUDY PLAN (with YouTube Chapters + Quiz)
# ═══════════════════════════════════════════════════════════

@router.post("/study-plan", response_model=StudyPlanResponse, status_code=201)
async def create_study_plan(
    data: StudyPlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Generate complete study plan with YouTube chapters + quiz.
    SINGLE API CALL - returns chapters with video links and quiz questions.
    """
    # Run blocking LLM API call in thread pool
    plan_data = await asyncio.to_thread(
        generate_study_plan_with_quiz, db, user.id, data.goal, data.duration_days, data.document_id
    )

    plan = StudyPlan(
        user_id=user.id,
        title=plan_data.get("title", data.goal[:100]),
        goal=data.goal,
        plan_data=plan_data,
        duration_days=data.duration_days,
        document_id=data.document_id,
        quiz_unlocked=False,  # Will unlock after all chapters completed
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    
    # Create chapter progress tracking entries
    chapters = plan_data.get("chapters", [])
    chapter_progress_list = []
    for chapter in chapters:
        chapter_progress = ChapterProgress(
            study_plan_id=plan.id,
            user_id=user.id,
            chapter_index=chapter.get("chapter_number", 0),
            chapter_title=chapter.get("title", ""),
            youtube_url=chapter.get("youtube_url", ""),
            keyword_importance=chapter.get("keyword_importance", {}),  # AI-generated importance scores
            is_completed=False,
        )
        db.add(chapter_progress)
        chapter_progress_list.append((chapter_progress, chapter))

    # ── Batch-generate semantic embeddings for all chapters ──
    model = _matching.get_model()
    if model:
        texts = [
            _matching.build_chapter_text(
                chapter_title=cp.chapter_title,
                keyword_importance=cp.keyword_importance,
                description=ch.get("description", ""),
            )
            for cp, ch in chapter_progress_list
        ]
        try:
            embeddings = await asyncio.to_thread(
                lambda: model.encode(texts, normalize_embeddings=True).tolist()
            )
            for (cp, _), emb in zip(chapter_progress_list, embeddings):
                cp.chapter_embedding = emb
        except Exception as e:
            print(f"[Embedding] Batch embedding failed: {e}")
    else:
        print("[Embedding] Model not available — chapters stored without embeddings")

    db.commit()
    return StudyPlanResponse.model_validate(plan)


@router.get("/study-plans", response_model=list[StudyPlanResponse])
def list_study_plans(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all study plans."""
    plans = db.query(StudyPlan).filter(
        StudyPlan.user_id == user.id
    ).order_by(StudyPlan.created_at.desc()).all()
    return [StudyPlanResponse.model_validate(p) for p in plans]


@router.get("/study-plan/{plan_id}", response_model=StudyPlanResponse)
def get_study_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a specific study plan with progress."""
    plan = db.query(StudyPlan).filter(
        StudyPlan.id == plan_id,
        StudyPlan.user_id == user.id,
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Study plan not found")
    
    # Add chapter progress info
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id
    ).all()
    
    # Inject progress into plan_data
    plan_dict = plan.plan_data.copy() if isinstance(plan.plan_data, dict) else {}
    if "chapters" in plan_dict:
        for chapter in plan_dict["chapters"]:
            chapter_num = chapter.get("chapter_number", 0)
            prog = next((p for p in progress if p.chapter_index == chapter_num), None)
            chapter["is_completed"] = prog.is_completed if prog else False
            chapter["completed_at"] = prog.completed_at.isoformat() if prog and prog.completed_at else None
    
    # Check if all chapters completed → unlock quiz
    if progress:
        all_completed = all(p.is_completed for p in progress)
        if all_completed and not plan.quiz_unlocked:
            plan.quiz_unlocked = True
            db.commit()
    
    return StudyPlanResponse.model_validate(plan)


# ═══════════════════════════════════════════════════════════
#  CHAPTER COMPLETION TRACKING
# ═══════════════════════════════════════════════════════════

@router.post("/study-plan/{plan_id}/chapter/{chapter_number}/complete")
def mark_chapter_complete(
    plan_id: str,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark a chapter as completed."""
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_index == chapter_number,
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    if not progress.is_completed:
        progress.is_completed = True
        progress.completed_at = datetime.now(timezone.utc)
        db.commit()
    
    # Check if all chapters completed
    all_progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id
    ).all()
    
    all_completed = all(p.is_completed for p in all_progress)
    
    # Unlock quiz if all done
    if all_completed:
        plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id).first()
        if plan and not plan.quiz_unlocked:
            plan.quiz_unlocked = True
            db.commit()
    
    return {
        "success": True,
        "chapter_completed": True,
        "all_chapters_completed": all_completed,
        "quiz_unlocked": all_completed
    }


@router.post("/study-plan/{plan_id}/chapter/{chapter_number}/reset")
def reset_chapter_progress(
    plan_id: str,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reset a chapter's progress completely.
    Re-opens it for fresh tracking: clears video info, watched time, completion status.
    """
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_index == chapter_number,
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    old_title = progress.youtube_title
    progress.youtube_url = None
    progress.youtube_title = None
    progress.video_duration_seconds = 0
    progress.watched_seconds = 0
    progress.creator_name = None
    progress.is_completed = False
    progress.completed_at = None
    
    # Re-lock quiz if this chapter was completed
    plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id).first()
    if plan and plan.quiz_unlocked:
        plan.quiz_unlocked = False
    
    db.commit()
    
    print(f"[Chapter Reset] Chapter {chapter_number}: '{old_title}' → RESET")
    return {
        "success": True,
        "message": f"Chapter {chapter_number} has been reset",
        "chapter_index": chapter_number,
    }


@router.get("/study-plan/{plan_id}/progress")
def get_chapter_progress(
    plan_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get progress for all chapters in a study plan."""
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id
    ).order_by(ChapterProgress.chapter_index).all()
    
    # Check if all chapters completed
    all_completed = len(progress) > 0 and all(p.is_completed for p in progress)
    
    # Auto-unlock quiz if all chapters done
    if all_completed:
        plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id).first()
        if plan and not plan.quiz_unlocked:
            plan.quiz_unlocked = True
            db.commit()
            print(f"[Quiz Auto-Unlock] All chapters completed for plan {plan_id}")
    
    # Get quiz unlock status
    plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id).first()
    quiz_unlocked = plan.quiz_unlocked if plan else False
    
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
                "keyword_importance": p.keyword_importance or {},  # AI-generated importance scores
                "is_completed": p.is_completed,
                "completed_at": p.completed_at.isoformat() if p.completed_at else None
            }
            for p in progress
        ],
        "total_chapters": len(progress),
        "completed_chapters": sum(1 for p in progress if p.is_completed),
        "quiz_unlocked": quiz_unlocked
    }


# ═══════════════════════════════════════════════════════════
#  VIDEO PROGRESS TRACKING
# ═══════════════════════════════════════════════════════════

@router.post("/study-plan/{plan_id}/chapter/{chapter_number}/update-progress")
def update_chapter_progress(
    plan_id: str,
    chapter_number: int,
    watched_seconds: int = Body(..., embed=True),
    video_ended: bool = Body(False, embed=True),
    video_duration_seconds: Optional[int] = Body(None, embed=True),
    video_title: Optional[str] = Body(None, embed=True),
    creator_name: Optional[str] = Body(None, embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update watch progress for a chapter (called by tracking system every 10 seconds).
    Real-time progress updates with exact video.currentTime tracking.
    Also backfills youtube_title and creator_name if missing.
    """
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_index == chapter_number,
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    # Backfill youtube_title if it's missing (migration support)
    if video_title and not progress.youtube_title:
        progress.youtube_title = video_title
        print(f"[Title Backfill] Chapter {chapter_number}: '{video_title}' (via progress update)")
    
    # Always update creator_name when provided (fixes stale channel name after video switch)
    if creator_name and progress.creator_name != creator_name:
        print(f"[Creator Update] Chapter {chapter_number}: '{progress.creator_name}' → '{creator_name}'")
        progress.creator_name = creator_name
    
    # Update video duration if a better (larger) value is provided by the extension
    # This corrects wrong durations from ads or early metadata detection
    if video_duration_seconds and video_duration_seconds > progress.video_duration_seconds:
        old_dur = progress.video_duration_seconds
        progress.video_duration_seconds = video_duration_seconds
        print(f"[Duration Fix] Chapter {chapter_number}: {old_dur}s → {video_duration_seconds}s")
    
    # ALLOW RE-WATCHING: Track time even for completed chapters (analytics)
    # But NEVER reduce watched_seconds on completed chapters (preserves progress bar)
    was_already_completed = progress.is_completed
    if progress.is_completed:
        progress.watched_seconds = max(progress.watched_seconds, watched_seconds)
    else:
        progress.watched_seconds = watched_seconds
    
    # Auto-complete if video ended OR watched >= 95% of video
    just_completed = False
    if progress.video_duration_seconds > 0:
        watch_percentage = (progress.watched_seconds / progress.video_duration_seconds) * 100
        
        # Mark complete if: video ended event OR watched 95%+
        if (video_ended or watch_percentage >= 95) and not progress.is_completed:
            progress.is_completed = True
            progress.completed_at = datetime.now(timezone.utc)
            just_completed = True
            print(f"[Chapter Complete] {progress.chapter_title} - {'Video ended' if video_ended else f'{watch_percentage:.1f}% watched'}")
            
            # Check if all chapters completed
            all_progress = db.query(ChapterProgress).filter(
                ChapterProgress.study_plan_id == plan_id,
                ChapterProgress.user_id == user.id
            ).all()
            
            all_completed = all(p.is_completed for p in all_progress)
            
            # Unlock quiz if all done
            if all_completed:
                plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id).first()
                if plan and not plan.quiz_unlocked:
                    plan.quiz_unlocked = True
                    print(f"[Quiz Unlocked] All chapters completed for plan {plan_id}")
    
    db.commit()
    
    return {
        "success": True,
        "watched_seconds": progress.watched_seconds,
        "video_duration_seconds": progress.video_duration_seconds,
        "progress_percentage": min((progress.watched_seconds / progress.video_duration_seconds * 100), 100) if progress.video_duration_seconds > 0 else 0,
        "is_completed": progress.is_completed,
        "just_completed": just_completed,
        "was_already_completed": was_already_completed
    }


@router.post("/study-plan/{plan_id}/chapter/{chapter_number}/set-video")
def set_chapter_video(
    plan_id: str,
    chapter_number: int,
    video_url: str = Body(..., embed=True),
    video_duration_seconds: int = Body(..., embed=True),
    video_id: str = Body(None, embed=True),
    video_title: str = Body(None, embed=True),
    creator_name: str = Body(None, embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set video URL and duration for a chapter (auto-called by tracker).
    IMPORTANT: If video changes (different URL/video_id), RESET progress to 0.
    This allows switching from Striver's video to another creator's video on same topic.
    LOCK: Completed chapters cannot be changed/reset.
    """
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_index == chapter_number,
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    # LOCK: Do not allow video changes on completed chapters
    # BUT allow filling in missing metadata (title, creator, duration fix)
    if progress.is_completed:
        updated = False
        if video_title and not progress.youtube_title:
            progress.youtube_title = video_title
            updated = True
            print(f"[Title Backfill] Chapter {chapter_number}: '{video_title}'")
        if creator_name and not progress.creator_name:
            progress.creator_name = creator_name
            updated = True
        # Fix bad duration (ad-captured durations are typically < 120s for real videos)
        if video_duration_seconds and video_duration_seconds > progress.video_duration_seconds:
            old_dur = progress.video_duration_seconds
            progress.video_duration_seconds = video_duration_seconds
            # Also update watched_seconds to match if it was capped by old bad duration
            progress.watched_seconds = max(progress.watched_seconds, video_duration_seconds)
            updated = True
            print(f"[Duration Fix] Completed chapter {chapter_number}: {old_dur}s → {video_duration_seconds}s")
        if updated:
            db.commit()
        return {
            "success": False,
            "message": "Chapter already completed - cannot change video",
            "youtube_url": progress.youtube_url,
            "video_duration_seconds": progress.video_duration_seconds,
            "creator_name": progress.creator_name,
            "youtube_title": progress.youtube_title,
            "progress_reset": False,
            "watched_seconds": progress.watched_seconds
        }
    
    # Check if video changed (different URL or video ID)
    video_changed = False
    if progress.youtube_url and progress.youtube_url != video_url:
        video_changed = True
        print(f"[Video Changed] Chapter {chapter_number}: {progress.youtube_url} → {video_url}")
    
    # Update video details
    progress.youtube_url = video_url
    progress.video_duration_seconds = video_duration_seconds
    if creator_name:
        progress.creator_name = creator_name
    if video_title:
        progress.youtube_title = video_title
    
    # RESET progress if video changed (user switched to different video on same topic)
    if video_changed:
        progress.watched_seconds = 0
        progress.is_completed = False
        progress.completed_at = None
        print(f"[Progress Reset] Chapter {chapter_number}: Starting fresh with new video")
    
    db.commit()
    
    return {
        "success": True,
        "youtube_url": progress.youtube_url,
        "video_duration_seconds": progress.video_duration_seconds,
        "creator_name": progress.creator_name,
        "progress_reset": video_changed,
        "watched_seconds": progress.watched_seconds
    }


@router.post("/study-plan/{plan_id}/chapter/{chapter_number}/reset")
def reset_chapter_video(
    plan_id: str,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reset a chapter's video assignment and progress.
    Clears: youtube_url, youtube_title, creator_name, watched_seconds, completion status.
    Preserves: chapter_title, chapter_embedding, keyword_importance (plan structure).
    """
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_index == chapter_number,
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    old_title = progress.youtube_title or progress.youtube_url or "(no video)"
    
    # Clear video assignment
    progress.youtube_url = None
    progress.youtube_title = None
    progress.creator_name = None
    progress.video_duration_seconds = 0
    
    # Clear progress
    progress.watched_seconds = 0
    progress.is_completed = False
    progress.completed_at = None
    
    # Un-unlock quiz if it was unlocked (since a chapter is now incomplete)
    plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id).first()
    if plan and plan.quiz_unlocked:
        plan.quiz_unlocked = False
    
    db.commit()
    
    print(f"[Chapter Reset] Chapter {chapter_number} '{progress.chapter_title}': cleared video '{old_title}'")
    
    return {
        "success": True,
        "message": f"Chapter {chapter_number} reset — video cleared, progress zeroed",
        "chapter_title": progress.chapter_title,
    }

# ═══════════════════════════════════════════════════════════
#  PENDING CHAPTER ASSIGNMENT (for "Search on YouTube" button)
# ═══════════════════════════════════════════════════════════

# In-memory store: user_id → pending chapter info
# Survives within server lifetime; cleared on restart (acceptable for this UX)
_pending_chapters = {}


@router.post("/set-pending-chapter")
def set_pending_chapter(
    plan_id: str = Body(..., embed=True),
    chapter_index: int = Body(..., embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark a chapter as pending for automatic video assignment.
    Called when user clicks 'Search on YouTube' from the learning page.
    The extension will consume this on the next YouTube video detection."""
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_index == chapter_index,
    ).first()

    if not progress:
        raise HTTPException(status_code=404, detail="Chapter not found")

    _pending_chapters[user.id] = {
        "plan_id": plan_id,
        "chapter_index": chapter_index,
        "chapter_title": progress.chapter_title,
        "is_completed": progress.is_completed,
    }
    print(f"[Pending] Set pending chapter: plan={plan_id}, ch={chapter_index}, title={progress.chapter_title}")

    return {"success": True, "pending": _pending_chapters[user.id]}


@router.get("/pending-chapter")
def get_pending_chapter(
    user: User = Depends(get_current_user),
):
    """Get and consume the pending chapter assignment.
    Returns pending info and clears it (one-time use)."""
    pending = _pending_chapters.pop(user.id, None)
    if not pending:
        return {"pending": None}
    print(f"[Pending] Consumed pending chapter: {pending['chapter_title']}")
    return {"pending": pending}


# ═══════════════════════════════════════════════════════════
#  SEMANTIC VIDEO → CHAPTER MATCHING
# ═══════════════════════════════════════════════════════════

@router.post("/match-video")
async def match_video_to_chapter(
    video_title: str = Body(..., embed=True),
    video_url: str = Body(..., embed=True),
    video_id: Optional[str] = Body(None, embed=True),
    video_description: Optional[str] = Body(None, embed=True),
    duration_seconds: int = Body(0, embed=True),
    channel_name: Optional[str] = Body(None, embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Semantic video-to-chapter matching using sentence embeddings.
    Called by the Chrome extension when a YouTube video is detected.

    Flow:
      1. Gather all user's chapters that have stored embeddings.
      2. Compute cosine similarity between video embedding and each chapter embedding.
      3. Return best match if similarity >= 0.60; otherwise return matched=False.
      4. If matched, persist video details to the chapter via set-video logic.

    Similarity thresholds:
      >= 0.70 → match       (auto-assign)
      0.60–0.70 → match     (needs_confirmation flag set, background ignores for now)
      < 0.60  → no match
    """
    # Gather all user's chapters that have embeddings
    all_chapters = db.query(ChapterProgress).filter(
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_embedding.isnot(None),
    ).all()

    if not all_chapters:
        return {"matched": False, "reason": "no_chapters_with_embeddings"}

    print(f"[MatchVideo] '{video_title[:60]}' → checking against {len(all_chapters)} chapters with embeddings")

    # Build list for matching_service
    chapter_items = [
        {
            "chapter_index": ch.chapter_index,
            "chapter_title": ch.chapter_title,
            "chapter_embedding": ch.chapter_embedding,
            "is_completed": ch.is_completed,
            "plan_id": ch.study_plan_id,
            "plan_title": "",
        }
        for ch in all_chapters
    ]

    # Run CPU-bound matching in thread pool
    match = await asyncio.to_thread(
        _matching.find_best_chapter,
        chapter_items,
        video_title,
        video_description,
    )

    if match is None:
        print(f"[MatchVideo] No match found for '{video_title[:60]}' — all chapters below threshold")
        return {"matched": False, "reason": "below_threshold"}

    plan_id = match["plan_id"]
    chapter_index = match["chapter_index"]
    similarity = match["similarity"]
    match_type = match["match_type"]

    # Retrieve the matched ChapterProgress row
    matched_ch = next(
        (ch for ch in all_chapters
         if ch.study_plan_id == plan_id and ch.chapter_index == chapter_index),
        None,
    )
    if matched_ch is None:
        return {"matched": False, "reason": "chapter_not_found"}

    # ── Persist video info to chapter (mirrors set-video logic) ──
    if not matched_ch.is_completed:
        video_changed = bool(matched_ch.youtube_url and matched_ch.youtube_url != video_url)
        matched_ch.youtube_url = video_url
        if duration_seconds > matched_ch.video_duration_seconds:
            matched_ch.video_duration_seconds = duration_seconds
        if channel_name:
            matched_ch.creator_name = channel_name
        if video_title:
            matched_ch.youtube_title = video_title
        if video_changed:
            matched_ch.watched_seconds = 0
            matched_ch.is_completed = False
            matched_ch.completed_at = None
            print(f"[SemanticMatch] Video changed on chapter {chapter_index} — progress reset")
    else:
        # Fill missing metadata on already-completed chapters (no unlock/reset)
        updated = False
        if video_title and not matched_ch.youtube_title:
            matched_ch.youtube_title = video_title
            updated = True
        if channel_name and not matched_ch.creator_name:
            matched_ch.creator_name = channel_name
            updated = True
        if duration_seconds and duration_seconds > matched_ch.video_duration_seconds:
            matched_ch.video_duration_seconds = duration_seconds
            updated = True
        if not updated:
            pass  # nothing to persist

    db.commit()

    # Fetch plan title for logging
    plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id).first()
    plan_title = plan.title if plan else ""

    print(
        f"[SemanticMatch] '{video_title[:60]}' → "
        f"'{matched_ch.chapter_title}' "
        f"sim={similarity:.3f} type={match_type}"
    )

    return {
        "matched": True,
        "plan_id": plan_id,
        "plan_title": plan_title,
        "chapter_index": chapter_index,
        "chapter_title": matched_ch.chapter_title,
        "similarity": round(similarity, 3),
        "match_type": match_type,
        "needs_confirmation": match_type == "needs_confirmation",
        "is_rewatch": matched_ch.is_completed,
    }


# ═══════════════════════════════════════════════════════════
#  QUIZ (Unlocked After Chapters)
# ═══════════════════════════════════════════════════════════

@router.post("/study-plan/{plan_id}/quiz/submit")
def submit_quiz(
    plan_id: str,
    answers: dict = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit quiz answers and get score."""
    plan = db.query(StudyPlan).filter(
        StudyPlan.id == plan_id,
        StudyPlan.user_id == user.id
    ).first()
    
    if not plan:
        raise HTTPException(status_code=404, detail="Study plan not found")
    
    if not plan.quiz_unlocked:
        raise HTTPException(status_code=403, detail="Complete all chapters first to unlock quiz")
    
    quiz_questions = plan.plan_data.get("quiz", [])
    if not quiz_questions:
        raise HTTPException(status_code=404, detail="No quiz found in this plan")
    
    # Auto-grade
    score = 0
    results = []
    
    for idx, question in enumerate(quiz_questions):
        user_answer = answers.get(str(idx))
        correct_answer = question.get("correct_answer", 0)
        is_correct = int(user_answer) == int(correct_answer) if user_answer is not None else False
        
        if is_correct:
            score += 1
        
        results.append({
            "question_number": idx,
            "correct": is_correct,
            "user_answer": user_answer,
            "correct_answer": correct_answer,
            "explanation": question.get("explanation", "")
        })
    
    score_pct = (score / len(quiz_questions)) * 100 if quiz_questions else 0
    
    # Save quiz attempt
    attempt = QuizAttempt(
        user_id=user.id,
        study_plan_id=plan_id,
        questions=quiz_questions,
        answers=answers,
        score=score_pct,
        max_score=len(quiz_questions),
        completed_at=datetime.now(timezone.utc)
    )
    db.add(attempt)
    db.commit()
    
    return {
        "score": score_pct,
        "total_questions": len(quiz_questions),
        "correct_answers": score,
        "results": results
    }


@router.get("/study-plan/{plan_id}/quiz-attempts")
def get_quiz_attempts(
    plan_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all quiz attempts for a study plan."""
    attempts = db.query(QuizAttempt).filter(
        QuizAttempt.study_plan_id == plan_id,
        QuizAttempt.user_id == user.id
    ).order_by(QuizAttempt.created_at.asc()).all()
    
    return {
        "attempts": [
            {
                "attempt_number": idx + 1,
                "score": attempt.score,
                "total_questions": int(attempt.max_score),
                "correct_answers": int((attempt.score / 100) * attempt.max_score) if attempt.max_score > 0 else 0,
                "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None
            }
            for idx, attempt in enumerate(attempts)
        ],
        "total_attempts": len(attempts),
        "best_score": max([a.score for a in attempts]) if attempts else 0,
        "latest_score": attempts[-1].score if attempts else 0
    }


@router.get("/quizzes", response_model=list[QuizResponse])
def list_quizzes(
    limit: int = Query(10, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List recent quiz attempts."""
    quizzes = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user.id
    ).order_by(QuizAttempt.created_at.desc()).limit(limit).all()
    return [QuizResponse.model_validate(q) for q in quizzes]
