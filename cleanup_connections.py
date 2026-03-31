"""
Quick cleanup script to remove old parent-child invite connections.
Run this to reset the connections before testing OTP flow.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.models import ParentChild
from app.config.settings import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"ssl": {"ssl_disabled": False}}
)
Session = sessionmaker(bind=engine)
db = Session()

try:
    # Delete all old invite-based connections
    deleted_count = db.query(ParentChild).delete()
    db.commit()
    print(f"✅ Deleted {deleted_count} old invite-based connections")
except Exception as e:
    db.rollback()
    print(f"❌ Error: {e}")
finally:
    db.close()
