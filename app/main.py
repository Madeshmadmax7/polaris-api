"""
LifeOS – FastAPI Main Application
Brings together all routes, WebSocket, CORS, and lifecycle events.

[V1] Basic Telemetry + Rule-Based Productivity
     Only auth, tracking, and productivity routes are active.
     Advanced features commented out for future versions.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.config.database import engine, Base
# [V1] Only core routes active
from app.routes import auth, tracking, productivity
# [V2+] Advanced routes — uncomment when ready
# from app.routes import parental, ai, parental_connection, notifications, knowledge, learning_path, knowledge_gap
from app.websocket.manager import ws_manager
# [V2+] Blocked list sync — uncomment when parental controls are enabled
# from app.websocket.manager import emit_blocked_list_sync
from app.utils.auth import decode_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — create tables, run migrations, preload ML model."""
    Base.metadata.create_all(bind=engine)

    # Safe migration: add new columns without breaking existing tables
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

        # [V2+] Migration 2: keyword_importance for chapter_progress
        # chapters_columns = [c['name'] for c in inspector.get_columns('chapter_progress')]
        # if 'keyword_importance' not in chapters_columns:
        #     try:
        #         conn.execute(text("ALTER TABLE chapter_progress ADD COLUMN keyword_importance JSON NULL"))
        #         conn.commit()
        #         print("[MIGRATE] Added keyword_importance column to chapter_progress")
        #     except Exception as e:
        #         print(f"[MIGRATE] keyword_importance column migration skipped: {e}")

        # [V2+] Migration 3: chapter_embedding for semantic video matching
        # chapters_columns = [c['name'] for c in inspector.get_columns('chapter_progress')]
        # if 'chapter_embedding' not in chapters_columns:
        #     try:
        #         conn.execute(text("ALTER TABLE chapter_progress ADD COLUMN chapter_embedding JSON NULL"))
        #         conn.commit()
        #         print("[MIGRATE] Added chapter_embedding column to chapter_progress")
        #     except Exception as e:
        #         print(f"[MIGRATE] chapter_embedding column migration skipped: {e}")

        # [V2+] Migration 4: playback_rate for video speed tracking
        # chapters_columns = [c['name'] for c in inspector.get_columns('chapter_progress')]
        # if 'playback_rate' not in chapters_columns:
        #     try:
        #         conn.execute(text("ALTER TABLE chapter_progress ADD COLUMN playback_rate FLOAT DEFAULT 1.0"))
        #         conn.commit()
        #         print("[MIGRATE] Added playback_rate column to chapter_progress")
        #     except Exception as e:
        #         print(f"[MIGRATE] playback_rate column migration skipped: {e}")

        # [V2+] Migration 5: ai_summary for chapter completion summaries
        # chapters_columns = [c['name'] for c in inspector.get_columns('chapter_progress')]
        # if 'ai_summary' not in chapters_columns:
        #     try:
        #         conn.execute(text("ALTER TABLE chapter_progress ADD COLUMN ai_summary TEXT NULL"))
        #         conn.commit()
        #         print("[MIGRATE] Added ai_summary column to chapter_progress")
        #     except Exception as e:
        #         print(f"[MIGRATE] ai_summary column migration skipped: {e}")

    # [V2+] Preload embedding model at startup (runs in background to avoid blocking)
    # This ensures the server starts immediately while the model loads.
    # import asyncio
    # from app.services import matching_service
    #
    # async def _init_model_and_backfill():
    #     model_ok = await asyncio.to_thread(matching_service.preload_model)
    #     if model_ok:
    #         print("[START] Embedding model preloaded successfully.")
    #         await asyncio.to_thread(_backfill_chapter_embeddings)
    #     else:
    #         print("[START] WARNING: Embedding model failed to load — semantic matching unavailable.")
    #
    # asyncio.create_task(_init_model_and_backfill())

    print(f"[START] {settings.APP_NAME} v{settings.APP_VERSION} (V1 – Telemetry + Productivity) starting...")
    yield
    print(f"[STOP] {settings.APP_NAME} shutting down...")


# [V2+] Embedding backfill — uncomment when AI/NLP features are enabled
# def _backfill_chapter_embeddings():
#     """
#     Generate and store chapter_embedding for any ChapterProgress rows that are missing it.
#     This runs once at startup in a background thread so new deployments self-heal.
#     """
#     from app.config.database import SessionLocal
#     from app.models.models import ChapterProgress
#     from app.services.matching_service import build_chapter_text, embed_text
#
#     db = SessionLocal()
#     try:
#         missing = db.query(ChapterProgress).filter(
#             ChapterProgress.chapter_embedding.is_(None)
#         ).all()
#
#         if not missing:
#             print("[Backfill] All chapters already have embeddings.")
#             return
#
#         print(f"[Backfill] Generating embeddings for {len(missing)} chapters...")
#         updated = 0
#         for ch in missing:
#             text = build_chapter_text(
#                 chapter_title=ch.chapter_title,
#                 keyword_importance=ch.keyword_importance,
#             )
#             emb = embed_text(text)
#             if emb:
#                 ch.chapter_embedding = emb
#                 updated += 1
#
#         db.commit()
#         print(f"[Backfill] Done — {updated}/{len(missing)} chapters embedded.")
#     except Exception as e:
#         print(f"[Backfill] Error during embedding backfill: {e}")
#         db.rollback()
#     finally:
#         db.close()


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
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://polaristracker.netlify.app",
    ],
    allow_origin_regex=r"https?://(.*\.)?(youtube\.com|netlify\.app)|chrome-extension://.*|https?://localhost:.*|https?://127\.0\.0\.1:.*",
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
# [V1] Core routes only
app.include_router(auth.router, prefix="/api")
app.include_router(tracking.router, prefix="/api")
app.include_router(productivity.router, prefix="/api")
# [V2+] Advanced routes — uncomment when ready
# app.include_router(parental.router, prefix="/api")
# app.include_router(parental_connection.router, prefix="/api")
# app.include_router(ai.router, prefix="/api")
# app.include_router(notifications.router, prefix="/api")
# app.include_router(knowledge.router, prefix="/api")
# app.include_router(learning_path.router, prefix="/api")
# app.include_router(knowledge_gap.router, prefix="/api")


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
        # [V2+] Send initial blocked sites sync — uncomment when parental controls are enabled
        # from app.config.database import SessionLocal
        # from app.services.parental_service import get_blocked_sites
        # db = SessionLocal()
        # try:
        #     blocked = get_blocked_sites(db, user_id)
        #     await emit_blocked_list_sync(user_id, blocked)
        # finally:
        #     db.close()

        # Listen for messages
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "heartbeat":
                await websocket.send_json({"type": "heartbeat_ack"})
            # [V2+] Blocked sites sync — uncomment when parental controls are enabled
            # elif msg_type == "sync_blocked":
            #     db = SessionLocal()
            #     try:
            #         blocked = get_blocked_sites(db, user_id)
            #         await emit_blocked_list_sync(user_id, blocked)
            #     finally:
            #         db.close()
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


# ── Root / Health Check ──────────────────────────────────
@app.get("/")
def root():
    """Root endpoint — Render's health check hits this."""
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


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
