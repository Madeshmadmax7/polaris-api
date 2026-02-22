"""
LifeOS – AI & RAG Routes
Document upload, study plan generation, quiz engine, RAG queries.
"""

import os
import shutil
import tempfile
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.models import User, StudyPlan, QuizAttempt
from app.schemas.schemas import (
    DocumentResponse, RAGQueryRequest, RAGQueryResponse,
    StudyPlanRequest, StudyPlanResponse,
    QuizGenerateRequest, QuizSubmitRequest, QuizResponse,
)
from app.utils.auth import get_current_user
from app.services.rag_service import process_document
from app.services.ai_service import (
    generate_study_plan, generate_quiz, rag_query,
)

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
    Processes: PDF → chunks → embeddings → FAISS index.
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

@router.post("/query", response_model=RAGQueryResponse)
async def query_documents(
    data: RAGQueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Query uploaded documents using RAG."""
    # Run blocking RAG query (embedding + FAISS search) in thread pool
    result = await asyncio.to_thread(rag_query, db, user.id, data.query, data.document_id, data.top_k)
    return RAGQueryResponse(**result)


# ═══════════════════════════════════════════════════════════
#  STUDY PLAN
# ═══════════════════════════════════════════════════════════

@router.post("/study-plan", response_model=StudyPlanResponse, status_code=201)
async def create_study_plan(
    data: StudyPlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a RAG-grounded study plan."""
    # Run blocking LLM API call in thread pool
    plan_data = await asyncio.to_thread(
        generate_study_plan, db, user.id, data.goal, data.duration_days, data.document_id
    )

    plan = StudyPlan(
        user_id=user.id,
        title=plan_data.get("title", data.goal[:100]),
        goal=data.goal,
        plan_data=plan_data,
        duration_days=data.duration_days,
        document_id=data.document_id,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

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
    """Get a specific study plan."""
    plan = db.query(StudyPlan).filter(
        StudyPlan.id == plan_id,
        StudyPlan.user_id == user.id,
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Study plan not found")
    return StudyPlanResponse.model_validate(plan)


# ═══════════════════════════════════════════════════════════
#  QUIZ ENGINE
# ═══════════════════════════════════════════════════════════

@router.post("/quiz/generate", response_model=QuizResponse, status_code=201)
async def create_quiz(
    data: QuizGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Generate a quiz with context from RAG.
    Requires minimum focus time validation (checked server-side).
    """
    topic = data.topic or "General review"

    # Generate quiz questions (blocking LLM API call, run in thread pool)
    quiz_data = await asyncio.to_thread(
        generate_quiz, db, user.id, topic, data.difficulty.value, data.document_id
    )

    # Create quiz attempt record
    attempt = QuizAttempt(
        user_id=user.id,
        study_plan_id=data.study_plan_id,
        questions=quiz_data.get("questions", []),
        difficulty=data.difficulty.value,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    return QuizResponse.model_validate(attempt)


@router.post("/quiz/submit")
def submit_quiz(
    data: QuizSubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit quiz answers and get score."""
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == data.quiz_id,
        QuizAttempt.user_id == user.id,
    ).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # Auto-grade MCQs
    score = 0
    max_score = 0
    results = []

    for q in attempt.questions:
        q_id = str(q.get("id", ""))
        q_type = q.get("type", "")

        if q_type == "mcq":
            max_score += 1
            user_answer = data.answers.get(q_id)
            correct = q.get("correct")
            is_correct = str(user_answer) == str(correct)
            if is_correct:
                score += 1
            results.append({
                "id": q_id,
                "correct": is_correct,
                "explanation": q.get("explanation", ""),
            })
        elif q_type in ("conceptual", "coding"):
            max_score += 2  # Worth more
            results.append({
                "id": q_id,
                "type": q_type,
                "needs_review": True,
                "rubric": q.get("rubric", ""),
            })

    # Normalize score to percentage
    score_pct = (score / max(max_score, 1)) * 100

    attempt.answers = data.answers
    attempt.score = score_pct
    attempt.max_score = max_score
    attempt.completed_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "score": score_pct,
        "max_score": max_score,
        "results": results,
    }


@router.get("/quizzes", response_model=list[QuizResponse])
def list_quizzes(
    limit: int = Query(10, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List recent quizzes."""
    quizzes = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user.id
    ).order_by(QuizAttempt.created_at.desc()).limit(limit).all()
    return [QuizResponse.model_validate(q) for q in quizzes]
