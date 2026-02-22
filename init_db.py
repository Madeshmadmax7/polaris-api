"""Create the lifeos database and all tables."""
import pymysql

# Create the database first
conn = pymysql.connect(host="localhost", user="root", password="root")
cursor = conn.cursor()
cursor.execute("CREATE DATABASE IF NOT EXISTS lifeos CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
cursor.close()
conn.close()
print("Database 'lifeos' created successfully!")

# Now create all tables
from app.config.database import engine, Base
from app.models.models import *

Base.metadata.create_all(bind=engine)
print("All tables created successfully!")

# List tables
conn = pymysql.connect(host="localhost", user="root", password="root", database="lifeos")
cursor = conn.cursor()
cursor.execute("SHOW TABLES")
tables = cursor.fetchall()
print(f"\nTables in lifeos ({len(tables)}):")
for t in tables:
    print(f"  - {t[0]}")
cursor.close()
conn.close()
