"""
LifeOS – Parental Control Routes
Ethical parental oversight with WebSocket-driven site blocking.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.models.models import User
from app.schemas.schemas import BlockSiteRequest, ChildOverview
from app.utils.auth import get_current_user, require_parent
from app.services.parental_service import (
    create_invite, accept_invite, get_children,
    get_child_overview, block_site, unblock_site, get_blocked_sites,
)
from app.websocket.manager import emit_block_event, emit_blocked_list_sync

router = APIRouter(prefix="/parental", tags=["Parental Control"])


@router.post("/invite")
async def invite_child(
    child_email: str,
    db: Session = Depends(get_db),
    parent: User = Depends(require_parent),
):
    """Generate an invite code for a child."""
    try:
        code = create_invite(db, parent.id, child_email)
        return {"invite_code": code}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/accept-invite")
async def accept_child_invite(
    invite_code: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Child accepts a parent's invite."""
    try:
        accept_invite(db, user.id, invite_code)
        return {"status": "accepted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/children")
async def list_children(
    db: Session = Depends(get_db),
    parent: User = Depends(require_parent),
):
    """List all linked children."""
    children = get_children(db, parent.id)
    return [
        {"id": c.id, "username": c.username, "email": c.email}
        for c in children
    ]


@router.get("/child/{child_id}")
async def child_overview(
    child_id: str,
    db: Session = Depends(get_db),
    parent: User = Depends(require_parent),
):
    """Get ethical child overview — domain trends and scores only."""
    try:
        return get_child_overview(db, parent.id, child_id)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/block")
async def block_domain(
    data: BlockSiteRequest,
    db: Session = Depends(get_db),
    parent: User = Depends(require_parent),
):
    """
    Block a domain for a child.
    Emits WebSocket event → Extension updates declarativeNetRequest rules.
    """
    try:
        blocked = block_site(db, parent.id, data.child_id, data.domain)

        # Emit real-time blocking event to child's extension
        await emit_block_event(data.child_id, data.domain, "block")

        return {
            "id": blocked.id,
            "domain": blocked.domain,
            "status": "blocked",
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized")


@router.post("/unblock/{blocked_site_id}")
async def unblock_domain(
    blocked_site_id: str,
    child_id: str,
    db: Session = Depends(get_db),
    parent: User = Depends(require_parent),
):
    """Unblock a site. Emits WebSocket event."""
    try:
        # Get the domain before unblocking
        from app.models.models import BlockedSite
        site = db.query(BlockedSite).filter(BlockedSite.id == blocked_site_id).first()
        domain = site.domain if site else ""

        unblock_site(db, parent.id, blocked_site_id)

        # Emit unblock event
        await emit_block_event(child_id, domain, "unblock")

        return {"status": "unblocked"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/blocked-sites/{child_id}")
async def list_blocked_sites(
    child_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get blocked sites for a child (used by extension for sync)."""
    domains = get_blocked_sites(db, child_id)
    return {"domains": domains}
