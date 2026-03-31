"""
LifeOS – Database Engine & Session Configuration
Supports both SQLite (dev) and MySQL (production).
"""

import re
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config.settings import settings

# Ensure we always use pymysql driver for MySQL connections.
# Some providers (e.g. Render) set DATABASE_URL as mysql:// which
# defaults to the mysqldb C-extension driver that isn't installed.
_db_url = settings.DATABASE_URL
if _db_url.startswith("mysql://") or _db_url.startswith("mysql+mysqldb://"):
    _db_url = re.sub(r"^mysql(\+mysqldb)?://", "mysql+pymysql://", _db_url)

connect_args = {}
engine_kwargs = {
    "echo": settings.DEBUG,
}

if _db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
else:
    engine_kwargs.update({
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    })

engine = create_engine(
    _db_url,
    connect_args=connect_args,
    **engine_kwargs,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency – yields a DB session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
