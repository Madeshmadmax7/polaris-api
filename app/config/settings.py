"""
LifeOS – Application Configuration
Environment-driven settings with sensible defaults.
"""

from pathlib import Path
from pydantic import ConfigDict
from pydantic_settings import BaseSettings
from typing import Optional

# Get the absolute path to the backend directory (.env is in backend directory)
BACKEND_DIR = Path(__file__).parent.parent.parent  # .../backend/
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────
    APP_NAME: str = "LifeOS"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production-please"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # ── Database ─────────────────────────────────────────
    DATABASE_URL: str = "DATABASE_URL=mysql+pymysql://umfdftqsjjwpprpc:19hDENaCl9QhhO8JNVYe@b4w3wmywgiev6hg1jly2-mysql.services.clever-cloud.com:3306/b4w3wmywgiev6hg1jly2"

    # ── AI Providers ─────────────────────────────────────
    OPENAI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    AI_PROVIDER: str = "groq"  # "openai" | "groq"
    AI_MODEL: str = "llama-3.3-70b-versatile"

    # ── RAG / Embeddings ─────────────────────────────────
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    FAISS_INDEX_DIR: str = "./faiss_indices"
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    TOP_K_RETRIEVAL: int = 5

    # ── Productivity Weights ─────────────────────────────
    PRODUCTIVE_WEIGHT: float = 1.0
    NEUTRAL_WEIGHT: float = 0.3
    DISTRACTING_WEIGHT: float = -0.5
    QUIZ_WEIGHT_K1: float = 0.2
    DISTRACTION_PENALTY_K2: float = 0.1

    # ── CORS ─────────────────────────────────────────────
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "chrome-extension://*",
        "https://www.youtube.com",  # For extension content scripts on YouTube
        "https://youtube.com",
        "https://polaristracker.netlify.app",
    ]

    # ── Server ───────────────────────────────────────────
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # ── WebSocket ────────────────────────────────────────
    WS_HEARTBEAT_INTERVAL: int = 5

    # ── Google OAuth 2.0 (for Calendar Integration) ──────
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None
    ENCRYPTION_KEY: Optional[str] = None

    model_config = ConfigDict(
        env_file=str(ENV_FILE),  # Use absolute path to .env
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore extra env vars not defined in Settings
    )


settings = Settings()
