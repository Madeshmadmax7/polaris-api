"""
Migration: Add youtube_title column to chapter_progress table.
Run once after updating models.py.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "lifeos.db")

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if column already exists
    cursor.execute("PRAGMA table_info(chapter_progress)")
    columns = [col[1] for col in cursor.fetchall()]

    if "youtube_title" not in columns:
        cursor.execute("ALTER TABLE chapter_progress ADD COLUMN youtube_title VARCHAR(500)")
        conn.commit()
        print("✓ Added youtube_title column to chapter_progress")
    else:
        print("youtube_title column already exists")

    conn.close()

if __name__ == "__main__":
    migrate()
