"""Create all tables in the local SQLite database (lifeos.db)."""

from app.config.database import engine, Base
from app.models.models import *

print("Creating tables in lifeos.db ...")
Base.metadata.create_all(bind=engine)
print("All tables created successfully!")

# List tables that were created
from sqlalchemy import inspect
inspector = inspect(engine)
tables = inspector.get_table_names()
print(f"\nTables in lifeos.db ({len(tables)}):")
for t in tables:
    print(f"  - {t}")
