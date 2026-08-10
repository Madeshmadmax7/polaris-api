from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.utils.auth import get_current_user
from app.services.gamification_service import (
    get_skill_tree_with_progress,
    get_user_badges,
    generate_daily_quests
)

router = APIRouter(prefix="/gamification", tags=["gamification"])

@router.get("/skills")
def get_skills(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Get the dynamic skill tree configured in the database."""
    return get_skill_tree_with_progress(db, user_id)

@router.get("/badges")
def get_badges(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Get all available badges and user's unlock status."""
    return get_user_badges(db, user_id)

@router.get("/quests")
def get_quests(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """Get user's daily quests."""
    return generate_daily_quests(db, user_id)
