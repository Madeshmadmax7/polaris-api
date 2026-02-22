"""
LifeOS – Parental Control Service
Ethical parental supervision: domain trends & scores only.
No search queries, video titles, chat content, or IDs exposed.
"""

import string
import random
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.models import User, ParentChild, BlockedSite, DailySummary
from app.services.productivity_service import get_productivity_trend


def generate_invite_code(length: int = 8) -> str:
    """Generate a random alphanumeric invite code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


def create_invite(db: Session, parent_id: str, child_email: str) -> str:
    """
    Create a parent-child invite link.
    Parent initiates, child must accept.
    """
    child = db.query(User).filter(
        User.email == child_email,
        User.role == "student"
    ).first()
    if not child:
        raise ValueError("Student not found with that email")

    # Check if already linked
    existing = db.query(ParentChild).filter(
        ParentChild.parent_id == parent_id,
        ParentChild.child_id == child.id,
    ).first()
    if existing:
        raise ValueError("Already linked to this student")

    invite_code = generate_invite_code()

    link = ParentChild(
        parent_id=parent_id,
        child_id=child.id,
        invite_code=invite_code,
        is_accepted=False,
    )
    db.add(link)
    db.commit()

    return invite_code


def accept_invite(db: Session, child_id: str, invite_code: str) -> bool:
    """Child accepts a parent's invite."""
    link = db.query(ParentChild).filter(
        ParentChild.child_id == child_id,
        ParentChild.invite_code == invite_code,
        ParentChild.is_accepted == False,
    ).first()

    if not link:
        raise ValueError("Invalid or expired invite code")

    link.is_accepted = True
    db.commit()
    return True


def get_children(db: Session, parent_id: str) -> List[User]:
    """Get all linked children for a parent."""
    links = db.query(ParentChild).filter(
        ParentChild.parent_id == parent_id,
        ParentChild.is_accepted == True,
    ).all()

    children = []
    for link in links:
        child = db.query(User).filter(User.id == link.child_id).first()
        if child:
            children.append(child)
    return children


def verify_parent_child_access(db: Session, parent_id: str, child_id: str) -> bool:
    """Verify that a parent has accepted access to a child."""
    link = db.query(ParentChild).filter(
        ParentChild.parent_id == parent_id,
        ParentChild.child_id == child_id,
        ParentChild.is_accepted == True,
    ).first()
    return link is not None


def get_child_overview(db: Session, parent_id: str, child_id: str) -> dict:
    """
    Get ethical child overview for parent dashboard.
    Shows: domain trends, productivity scores, focus factor.
    Never shows: search queries, video titles, chat content.
    """
    if not verify_parent_child_access(db, parent_id, child_id):
        raise PermissionError("Not authorized to view this student's data")

    child = db.query(User).filter(User.id == child_id).first()
    if not child:
        raise ValueError("Student not found")

    # Get recent productivity
    trend = get_productivity_trend(db, child_id, days=7)
    latest = trend["scores"][-1] if trend["scores"] else {}

    # Get blocked sites
    blocked = db.query(BlockedSite).filter(
        BlockedSite.child_id == child_id,
        BlockedSite.parent_id == parent_id,
        BlockedSite.is_active == True,
    ).all()

    return {
        "child_id": child_id,
        "username": child.username,
        "productivity_score": latest.get("productivity_score", 0),
        "focus_factor": latest.get("focus_factor", 0),
        "total_active_minutes": latest.get("total_active_minutes", 0),
        "top_domains": latest.get("top_domains", []),
        "blocked_sites": [
            {
                "id": b.id,
                "domain": b.domain,
                "is_active": b.is_active,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in blocked
        ],
        "trend": trend,
    }


# ═══════════════════════════════════════════════════════════
#  SITE BLOCKING
# ═══════════════════════════════════════════════════════════

def block_site(db: Session, parent_id: str, child_id: str, domain: str) -> BlockedSite:
    """Block a domain for a child. Triggers WebSocket update."""
    if not verify_parent_child_access(db, parent_id, child_id):
        raise PermissionError("Not authorized")

    # Clean domain
    domain = domain.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]

    # Check if already blocked
    existing = db.query(BlockedSite).filter(
        BlockedSite.parent_id == parent_id,
        BlockedSite.child_id == child_id,
        BlockedSite.domain == domain,
    ).first()

    if existing:
        existing.is_active = True
        db.commit()
        db.refresh(existing)
        return existing

    blocked = BlockedSite(
        parent_id=parent_id,
        child_id=child_id,
        domain=domain,
        is_active=True,
    )
    db.add(blocked)
    db.commit()
    db.refresh(blocked)
    return blocked


def unblock_site(db: Session, parent_id: str, blocked_site_id: str) -> bool:
    """Unblock a site. Triggers WebSocket update."""
    site = db.query(BlockedSite).filter(
        BlockedSite.id == blocked_site_id,
        BlockedSite.parent_id == parent_id,
    ).first()

    if not site:
        raise ValueError("Blocked site not found")

    site.is_active = False
    db.commit()
    return True


def get_blocked_sites(db: Session, child_id: str) -> List[str]:
    """Get all actively blocked domains for a child (used by extension)."""
    sites = db.query(BlockedSite).filter(
        BlockedSite.child_id == child_id,
        BlockedSite.is_active == True,
    ).all()
    return [s.domain for s in sites]
