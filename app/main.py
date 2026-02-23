"""
LifeOS – FastAPI Main Application
Brings together all routes, WebSocket, CORS, and lifecycle events.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.config.database import engine, Base
from app.routes import auth, tracking, productivity, parental, ai
from app.websocket.manager import ws_manager, emit_blocked_list_sync
from app.utils.auth import decode_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — create tables on startup."""
    Base.metadata.create_all(bind=engine)

    # Safe migration: add page_title column if it doesn't exist
    # (create_all won't add new columns to existing tables)
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)
        
        # Migration 1: page_title for tracking_logs
        columns = [c['name'] for c in inspector.get_columns('tracking_logs')]
        if 'page_title' not in columns:
            try:
                conn.execute(text("ALTER TABLE tracking_logs ADD COLUMN page_title VARCHAR(500) NULL"))
                conn.commit()
                print("[MIGRATE] Added page_title column to tracking_logs")
            except Exception as e:
                print(f"[MIGRATE] page_title column migration skipped: {e}")
        
        # Migration 2: keyword_importance for chapter_progress
        chapters_columns = [c['name'] for c in inspector.get_columns('chapter_progress')]
        if 'keyword_importance' not in chapters_columns:
            try:
                conn.execute(text("ALTER TABLE chapter_progress ADD COLUMN keyword_importance JSON NULL"))
                conn.commit()
                print("[MIGRATE] Added keyword_importance column to chapter_progress")
            except Exception as e:
                print(f"[MIGRATE] keyword_importance column migration skipped: {e}")

    print(f"[START] {settings.APP_NAME} v{settings.APP_VERSION} starting...")
    yield
    print(f"[STOP] {settings.APP_NAME} shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Hybrid AI-Powered Digital Well-Being, Adaptive Learning & Ethical Parental Control Platform",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────
# Allow all origins for extension content scripts (they run in web page context)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(.*\.)?youtube\.com|chrome-extension://.*|https?://localhost:.*|https?://127\.0\.0\.1:.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Middleware ──
@app.middleware("http")
async def log_requests(request, call_next):
    print(f"DEBUG: Incoming {request.method} to {request.url}")
    response = await call_next(request)
    return response

# ── Routes ───────────────────────────────────────────────
app.include_router(auth.router, prefix="/api")
app.include_router(tracking.router, prefix="/api")
app.include_router(productivity.router, prefix="/api")
app.include_router(parental.router, prefix="/api")
app.include_router(ai.router, prefix="/api")


# ── WebSocket Endpoint ──────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
):
    """
    WebSocket connection endpoint.
    Authenticates via JWT token in query param.
    Used for: blocking rule sync, live productivity, notifications.
    """
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            print(f"[WS Auth] Invalid token for connection attempt")
            await websocket.close(code=4001, reason="Invalid token")
            return
    except Exception as e:
        print(f"[WS Auth] Authentication error: {e}")
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await ws_manager.connect(websocket, user_id)

    try:
        # Send initial blocked sites sync
        from app.config.database import SessionLocal
        from app.services.parental_service import get_blocked_sites
        db = SessionLocal()
        try:
            blocked = get_blocked_sites(db, user_id)
            await emit_blocked_list_sync(user_id, blocked)
        finally:
            db.close()

        # Listen for messages
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "heartbeat":
                await websocket.send_json({"type": "heartbeat_ack"})
            elif msg_type == "sync_blocked":
                # Re-send blocked list
                db = SessionLocal()
                try:
                    blocked = get_blocked_sites(db, user_id)
                    await emit_blocked_list_sync(user_id, blocked)
                finally:
                    db.close()
            elif msg_type == "live_activity":
                # Extension reporting live browsing - relay to all user connections
                live_data = data.get("data", {})
                print(f"[WS Live] {user_id}: {live_data.get('domain')} - \"{live_data.get('page_title')}\" ({live_data.get('category')})")
                await ws_manager.send_to_user(user_id, {
                    "type": "live_tracking",
                    "data": live_data,
                })

    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket, user_id)
    except Exception as e:
        print(f"[WS Error] {e}")
        await ws_manager.disconnect(websocket, user_id)


# ── Health Check ─────────────────────────────────────────
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "ws_connections": ws_manager.active_count,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
