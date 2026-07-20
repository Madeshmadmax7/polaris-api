"""
LifeOS – Learning Path Discovery API Routes
Endpoints for querying discovered learning paths and triggering re-analysis.

All endpoints are under /api/learning-path/.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.models import User, LearningPath
from app.schemas.schemas import (
    LearningPathResponse,
    LearningPathNodeResponse,
    LearningPathSummaryResponse,
    LearningPathHistoryEntry,
)
from app.utils.auth import get_current_user
from app.services.learning_path_service import (
    discover_learning_paths,
    get_learning_paths,
    get_learning_path_summary,
    get_learning_path_history,
    generate_path_description,
)

router = APIRouter(prefix="/learning-path", tags=["Learning Path Discovery"])


# ═══════════════════════════════════════════════════════════
#  GET /api/learning-path — Primary endpoint
# ═══════════════════════════════════════════════════════════

@router.get("")
def get_paths(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all discovered learning paths with enriched node data."""
    summary = get_learning_path_summary(db, user.id)
    paths_data = get_learning_paths(db, user.id)

    paths = []
    for item in paths_data:
        path = item['path']
        nodes = [LearningPathNodeResponse(**n) for n in item['nodes']]
        paths.append(LearningPathResponse(
            id=path.id,
            path_name=path.path_name,
            path_slug=path.path_slug,
            description=path.description,
            confidence=path.confidence,
            status=path.status,
            is_primary=path.is_primary,
            stage=path.stage,
            completion_pct=path.completion_pct,
            total_concepts=path.total_concepts,
            mastered_concepts=path.mastered_concepts,
            missing_topics=path.missing_topics or [],
            milestone_history=path.milestone_history or [],
            detected_at=path.detected_at,
            last_updated=path.last_updated,
            nodes=nodes,
        ))

    return {
        "summary": summary,
        "paths": [p.model_dump() for p in paths],
    }


# ═══════════════════════════════════════════════════════════
#  GET /api/learning-path/history — Historical evolution
# ═══════════════════════════════════════════════════════════

@router.get("/history")
def get_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get historical evolution of discovered learning paths."""
    history = get_learning_path_history(db, user.id)
    return {"history": history}


# ═══════════════════════════════════════════════════════════
#  POST /api/learning-path/analyze — Trigger re-analysis
# ═══════════════════════════════════════════════════════════

@router.post("/analyze", status_code=202)
def trigger_analysis(
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    """Trigger a background re-analysis of the knowledge graph
    to discover or update learning paths.
    Returns 202 immediately."""
    background_tasks.add_task(discover_learning_paths, user_id=user.id)
    return {"status": "queued", "message": "Learning path analysis queued"}


# ═══════════════════════════════════════════════════════════
#  POST /api/learning-path/{path_id}/describe — AI description
# ═══════════════════════════════════════════════════════════

@router.post("/{path_id}/describe")
def describe_path(
    path_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate an AI description for a learning path on demand."""
    path = db.query(LearningPath).filter(
        LearningPath.id == path_id,
        LearningPath.user_id == user.id,
    ).first()

    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")

    description = generate_path_description(db, user.id, path_id)

    return {
        "path_id": path_id,
        "path_name": path.path_name,
        "description": description or "Unable to generate description.",
    }
