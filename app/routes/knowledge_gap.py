"""
LifeOS – Knowledge Gap Detection API Routes
Endpoints for querying knowledge gaps, history, and AI recommendations.

All endpoints are under /api/knowledge-gaps/.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.models import User
from app.schemas.schemas import (
    KnowledgeGapResponse,
    KnowledgeGapSummaryResponse,
    KnowledgeGapHistoryEntry,
    KnowledgeGapRecommendationResponse
)
from app.utils.auth import get_current_user
from app.services.knowledge_gap_service import (
    get_knowledge_gaps,
    get_knowledge_gaps_history,
    get_knowledge_gap_summary,
    generate_gap_recommendations,
    analyze_knowledge_gaps
)

router = APIRouter(prefix="/knowledge-gaps", tags=["Knowledge Gap Detection"])


# ═══════════════════════════════════════════════════════════
#  GET /api/knowledge-gaps — Active gaps
# ═══════════════════════════════════════════════════════════

@router.get("")
def get_active_gaps(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all unresolved knowledge gaps ordered by severity."""
    summary = get_knowledge_gap_summary(db, user.id)
    gaps = get_knowledge_gaps(db, user.id)

    return {
        "summary": summary,
        "gaps": [KnowledgeGapResponse.model_validate(g).model_dump() for g in gaps]
    }


# ═══════════════════════════════════════════════════════════
#  GET /api/knowledge-gaps/history — Resolved gaps
# ═══════════════════════════════════════════════════════════

@router.get("/history")
def get_gap_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get historical evolution of resolved knowledge gaps."""
    gaps = get_knowledge_gaps_history(db, user.id)
    return {
        "history": [KnowledgeGapHistoryEntry.model_validate(g).model_dump() for g in gaps]
    }


# ═══════════════════════════════════════════════════════════
#  GET /api/knowledge-gaps/recommendations — AI Action items
# ═══════════════════════════════════════════════════════════

@router.get("/recommendations")
def get_gap_recommendations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate intelligent study recommendations for the top priority gaps."""
    recommendations = generate_gap_recommendations(db, user.id)
    return {"recommendations": recommendations}


# ═══════════════════════════════════════════════════════════
#  POST /api/knowledge-gaps/analyze — Force trigger detection
# ═══════════════════════════════════════════════════════════

@router.post("/analyze", status_code=202)
def trigger_gap_analysis(
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    """Trigger a background analysis to detect new knowledge gaps.
    Note: This is automatically run when knowledge is ingested."""
    background_tasks.add_task(analyze_knowledge_gaps, user_id=user.id)
    return {"status": "queued", "message": "Knowledge gap detection queued"}
