"""
LifeOS – WebSocket Manager
Real-time communication layer for:
- Blocking rule propagation (parent → extension)
- Live productivity updates
- System notifications
"""

import json
import asyncio
from typing import Dict, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """
    Manages WebSocket connections per user.
    Supports multiple connections per user (multiple tabs/devices).
    """

    def __init__(self):
        # user_id → set of WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            if user_id not in self._connections:
                self._connections[user_id] = set()
            self._connections[user_id].add(websocket)
        print(f"[WS] Connected: {user_id} ({len(self._connections[user_id])} active)")

    async def disconnect(self, websocket: WebSocket, user_id: str):
        """Remove a WebSocket connection."""
        async with self._lock:
            if user_id in self._connections:
                self._connections[user_id].discard(websocket)
                if not self._connections[user_id]:
                    del self._connections[user_id]
        print(f"[WS] Disconnected: {user_id}")

    async def send_to_user(self, user_id: str, message: dict):
        """Send a message to all connections of a specific user."""
        if user_id not in self._connections:
            return

        dead_connections = set()
        for ws in self._connections[user_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead_connections.add(ws)

        # Clean up dead connections
        if dead_connections:
            async with self._lock:
                if user_id in self._connections:
                    self._connections[user_id] -= dead_connections

    async def broadcast(self, message: dict):
        """Broadcast message to all connected users."""
        for user_id in list(self._connections.keys()):
            await self.send_to_user(user_id, message)

    def is_connected(self, user_id: str) -> bool:
        """Check if a user has any active connections."""
        return user_id in self._connections and len(self._connections[user_id]) > 0

    @property
    def active_count(self) -> int:
        """Total number of active connections."""
        return sum(len(conns) for conns in self._connections.values())


# ── Singleton Instance ───────────────────────────────────
ws_manager = ConnectionManager()


# ═══════════════════════════════════════════════════════════
#  EVENT TYPES
# ═══════════════════════════════════════════════════════════

class WSEvent:
    """WebSocket event types."""
    SITE_BLOCKED = "site_blocked"
    SITE_UNBLOCKED = "site_unblocked"
    BLOCKED_LIST_SYNC = "blocked_list_sync"
    PRODUCTIVITY_UPDATE = "productivity_update"
    QUIZ_AVAILABLE = "quiz_available"
    STUDY_PLAN_READY = "study_plan_ready"
    HEARTBEAT = "heartbeat"
    LIVE_TRACKING = "live_tracking"


async def emit_block_event(child_id: str, domain: str, action: str = "block"):
    """
    Emit site blocking event to child's extension.
    Extension uses this to update declarativeNetRequest rules.
    """
    event_type = WSEvent.SITE_BLOCKED if action == "block" else WSEvent.SITE_UNBLOCKED
    await ws_manager.send_to_user(child_id, {
        "type": event_type,
        "data": {
            "domain": domain,
            "action": action,
        },
    })


async def emit_blocked_list_sync(child_id: str, domains: list):
    """Send full blocked list to child's extension for sync."""
    await ws_manager.send_to_user(child_id, {
        "type": WSEvent.BLOCKED_LIST_SYNC,
        "data": {
            "domains": domains,
        },
    })


async def emit_productivity_update(user_id: str, score: float, focus_factor: float):
    """Notify dashboard of productivity score update."""
    await ws_manager.send_to_user(user_id, {
        "type": WSEvent.PRODUCTIVITY_UPDATE,
        "data": {
            "productivity_score": score,
            "focus_factor": focus_factor,
        },
    })
