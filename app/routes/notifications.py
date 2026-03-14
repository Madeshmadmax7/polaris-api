"""
Notifications Routes
In-app notification retrieval and management.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.config.database import get_db
from app.models.models import User, Notification
from app.utils.auth import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def get_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 20,
):
    """
    Get all notifications for current user, ordered by most recent first.
    Returns unread count + list of notifications.
    """
    notifications = db.query(Notification).filter(
        Notification.user_id == user.id
    ).order_by(desc(Notification.created_at)).limit(limit).all()
    
    unread_count = db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.is_read == False
    ).count()
    
    return {
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "data": n.data,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ],
        "unread_count": unread_count,
    }


@router.post("/{notification_id}/read")
def mark_as_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark a notification as read."""
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user.id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    notification.is_read = True
    db.commit()
    
    return {"success": True, "message": "Marked as read"}


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a notification."""
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user.id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    db.delete(notification)
    db.commit()
    
    return {"success": True, "message": "Notification deleted"}


@router.post("/clear-all")
def clear_all_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Clear all notifications for current user."""
    db.query(Notification).filter(
        Notification.user_id == user.id
    ).delete()
    db.commit()
    
    return {"success": True, "message": "All notifications cleared"}
