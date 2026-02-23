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
                "youtube_url": p.youtube_url,
                "video_duration_seconds": p.video_duration_seconds,
                "watched_seconds": p.watched_seconds,
                "progress_percentage": (p.watched_seconds / p.video_duration_seconds * 100) if p.video_duration_seconds > 0 else 0,
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
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update watch progress for a chapter (called by tracking system every 10 seconds).
    Real-time progress updates with exact video.currentTime tracking.
    """
    progress = db.query(ChapterProgress).filter(
        ChapterProgress.study_plan_id == plan_id,
        ChapterProgress.user_id == user.id,
        ChapterProgress.chapter_index == chapter_number,
    ).first()
    
    if not progress:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    # ALLOW RE-WATCHING: Update watched seconds even for completed chapters
    # Users can rewatch completed videos and progress should update
    progress.watched_seconds = watched_seconds
    
    # Auto-complete if video ended OR watched >= 95% of video
    if progress.video_duration_seconds > 0:
        watch_percentage = (progress.watched_seconds / progress.video_duration_seconds) * 100
        
        # Mark complete if: video ended event OR watched 95%+
        if (video_ended or watch_percentage >= 95) and not progress.is_completed:
            progress.is_completed = True
            progress.completed_at = datetime.now(timezone.utc)
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
        "progress_percentage": (progress.watched_seconds / progress.video_duration_seconds * 100) if progress.video_duration_seconds > 0 else 0,
        "is_completed": progress.is_completed
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
    if progress.is_completed:
        return {
            "success": False,
            "message": "Chapter already completed - cannot change video",
            "youtube_url": progress.youtube_url,
            "video_duration_seconds": progress.video_duration_seconds,
            "creator_name": progress.creator_name,
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
