"""
Parental Connection Service
Handles OTP generation, verification, expiry, and connection lifecycle.
"""

import random
import string
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models.models import ParentChildConnection, User, Notification


OTP_VALIDITY_MINUTES = 2


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalize DB datetime (naive or aware) into aware UTC datetime."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def generate_otp() -> str:
    """Generate a random 4-digit OTP."""
    return ''.join(random.choices(string.digits, k=4))


def create_connection_request(
    db: Session,
    parent_id: str,
    child_email: str,
) -> dict:
    """
    Create a pending connection request with OTP.
    OTP is sent ONLY to child's notification, NOT to parent.
    
    Returns: {connection_id, message}
    Raises: ValueError if child not found or connection already exists
    """
    # 1. Find child by email
    child = db.query(User).filter(User.email == child_email).first()
    if not child:
        raise ValueError("Child user not found")
    
    now = datetime.now(timezone.utc)

    # 2. Check existing active/pending connections for this pair
    existing_connections = db.query(ParentChildConnection).filter(
        ParentChildConnection.parent_id == parent_id,
        ParentChildConnection.child_id == child.id,
        ParentChildConnection.status.in_(["pending", "active"])
    ).all()

    for existing in existing_connections:
        # Always allow OTP regeneration: invalidate any old pending request first.
        if existing.status == "pending":
            existing.status = "expired"
            continue

        # Active connection: if expired mark it; otherwise block duplicate connect.
        if existing.status == "active":
            expires_at = _as_utc(existing.expires_at)
            if expires_at and now > expires_at:
                existing.status = "expired"
            else:
                raise ValueError("Connection already active")

    if existing_connections:
        db.flush()
    
    # 3. Generate OTP
    otp = generate_otp()
    
    # 4. Create connection record
    connection = ParentChildConnection(
        parent_id=parent_id,
        child_id=child.id,
        otp_code=otp,
        otp_created_at=now,
        status="pending"
    )
    db.add(connection)
    db.flush()  # Get the connection ID before commit
    
    # 5. Get parent info for notification messages
    parent = db.query(User).filter(User.id == parent_id).first()
    parent_name = parent.username if parent else "Unknown"
    
    # 6. Create notification for CHILD with OTP visible
    child_notification = Notification(
        user_id=child.id,
        type="parent_connection_request",
        title=f"{parent_name} is requesting to connect to your account",
        message=f"Your OTP is: {otp}. Share this with the parent.",
        data={
            "parent_id": parent_id,
            "parent_name": parent_name,
            "connection_id": connection.id,
            "otp_code": otp
        }
    )
    db.add(child_notification)
    
    # 7. Create notification for PARENT that request was sent
    parent_notification = Notification(
        user_id=parent_id,
        type="connection_request_sent",
        title="Connection request sent",
        message=f"Waiting for {child.username} to share the OTP with you. Click here to verify the OTP.",
        data={
            "child_id": child.id,
            "child_name": child.username,
            "connection_id": connection.id
        }
    )
    db.add(parent_notification)
    db.commit()
    
    return {
        "connection_id": connection.id,
        "message": f"Connection request sent to {child.username}. Waiting for OTP..."
    }


def get_connection_request(
    db: Session,
    connection_id: str,
    child_id: str,
) -> dict:
    """
    Retrieve connection request details for child.
    Used to display OTP to child.
    """
    connection = db.query(ParentChildConnection).filter(
        ParentChildConnection.id == connection_id,
        ParentChildConnection.child_id == child_id,
        ParentChildConnection.status == "pending"
    ).first()
    
    if not connection:
        raise ValueError("Connection request not found or expired")
    
    # Check OTP age (must be < 2 minutes)
    otp_created_at = _as_utc(connection.otp_created_at)
    age = datetime.now(timezone.utc) - otp_created_at
    if age > timedelta(minutes=OTP_VALIDITY_MINUTES):
        connection.status = "expired"
        db.commit()
        raise ValueError("OTP has expired. Request a new connection.")
    
    parent = db.query(User).filter(User.id == connection.parent_id).first()
    
    return {
        "parent_name": parent.username if parent else "Unknown",
        "otp_code": connection.otp_code,
        "expires_in_seconds": int((timedelta(minutes=OTP_VALIDITY_MINUTES) - age).total_seconds())
    }


def verify_connection(
    db: Session,
    connection_id: str,
    parent_id: str,
    otp_code: str,
) -> dict:
    """
    Verify OTP and activate connection.
    """
    connection = db.query(ParentChildConnection).filter(
        ParentChildConnection.id == connection_id,
        ParentChildConnection.parent_id == parent_id,
        ParentChildConnection.status == "pending"
    ).first()
    
    if not connection:
        raise ValueError("Connection request not found")
    
    # Check OTP age
    otp_created_at = _as_utc(connection.otp_created_at)
    age = datetime.now(timezone.utc) - otp_created_at
    if age > timedelta(minutes=OTP_VALIDITY_MINUTES):
        connection.status = "expired"
        db.commit()
        raise ValueError("OTP has expired. Request a new connection.")
    
    # Verify OTP
    if connection.otp_code != otp_code:
        raise ValueError("Invalid OTP")
    
    # Activate connection
    now = datetime.now(timezone.utc)
    connection.verified = True
    connection.status = "active"
    connection.connected_at = now
    connection.expires_at = now + timedelta(days=30)
    
    # Notify child of successful connection
    child_notification = Notification(
        user_id=connection.child_id,
        type="connection_verified",
        title="Parent Connected",
        message="A parent has successfully verified connection to your account.",
        data={"parent_id": parent_id}
    )
    db.add(child_notification)
    
    # Notify parent of successful connection
    parent_notification = Notification(
        user_id=parent_id,
        type="connection_verified",
        title="Connection Established",
        message="Your connection has been verified. You can now view analytics.",
        data={"child_id": connection.child_id}
    )
    db.add(parent_notification)
    
    db.commit()
    
    child = db.query(User).filter(User.id == connection.child_id).first()
    
    return {
        "connection_id": connection.id,
        "child_id": connection.child_id,
        "child_name": child.username if child else "Unknown",
        "verified": True,
        "expires_at": connection.expires_at.isoformat()
    }


def check_connection_validity(
    db: Session,
    parent_id: str,
    child_id: str,
) -> bool:
    """
    Check if a parent-child connection is active and valid.
    """
    connection = db.query(ParentChildConnection).filter(
        ParentChildConnection.parent_id == parent_id,
        ParentChildConnection.child_id == child_id,
        ParentChildConnection.status == "active"
    ).first()
    
    if not connection:
        return False
    
    # Check expiry
    now = datetime.now(timezone.utc)
    expires_at = _as_utc(connection.expires_at)
    if expires_at and now > expires_at:
        connection.status = "expired"
        db.commit()
        
        # Notify parent of expiry
        notification = Notification(
            user_id=parent_id,
            type="connection_expired",
            title="Connection Expired",
            message="Your connection with this child has expired. Please reconnect.",
            data={"child_id": child_id}
        )
        db.add(notification)
        db.commit()
        
        return False
    
    return True


def get_active_connections(db: Session, user_id: str, as_parent: bool = True) -> list:
    """
    Get all active connections for a user.
    as_parent=True: get children connections
    as_parent=False: get parent connections
    """
    now = datetime.now(timezone.utc)
    
    if as_parent:
        connections = db.query(ParentChildConnection).filter(
            ParentChildConnection.parent_id == user_id,
            ParentChildConnection.status == "active",
            ParentChildConnection.expires_at > now
        ).all()
    else:
        connections = db.query(ParentChildConnection).filter(
            ParentChildConnection.child_id == user_id,
            ParentChildConnection.status == "active",
            ParentChildConnection.expires_at > now
        ).all()
    
    result = []
    for conn in connections:
        if as_parent:
            other_user = db.query(User).filter(User.id == conn.child_id).first()
            role = "child"
        else:
            other_user = db.query(User).filter(User.id == conn.parent_id).first()
            role = "parent"
        
        result.append({
            "connection_id": conn.id,
            "user_id": other_user.id if other_user else None,
            "username": other_user.username if other_user else "Unknown",
            "email": other_user.email if other_user else None,
            "role": role,
            "connected_at": _as_utc(conn.connected_at).isoformat() if conn.connected_at else None,
            "expires_at": _as_utc(conn.expires_at).isoformat() if conn.expires_at else None,
            "expires_in_days": (_as_utc(conn.expires_at) - now).days if conn.expires_at else 0
        })
    
    return result


def disconnect_connection(
    db: Session,
    connection_id: str,
    user_id: str,
) -> dict:
    """
    Disconnect a parent-child connection.
    """
    connection = db.query(ParentChildConnection).filter(
        ParentChildConnection.id == connection_id
    ).first()
    
    if not connection:
        raise ValueError("Connection not found")
    
    # Only parent or child can disconnect
    if connection.parent_id != user_id and connection.child_id != user_id:
        raise ValueError("Unauthorized")
    
    connection.status = "expired"
    connection.expires_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"message": "Connection disconnected"}
