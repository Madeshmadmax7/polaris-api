"""
Add keyword_importance column to chapter_progress table
Run this once to migrate existing database
"""

import sqlite3
import json

def migrate():
    conn = sqlite3.connect('lifeos.db')
    cursor = conn.cursor()
    
    # Check if column exists
    cursor.execute("PRAGMA table_info(chapter_progress)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'keyword_importance' not in columns:
        print("[MIGRATE] Adding keyword_importance column...")
        cursor.execute("ALTER TABLE chapter_progress ADD COLUMN keyword_importance TEXT")
        conn.commit()
        print("[MIGRATE] ✓ keyword_importance column added")
    else:
        print("[MIGRATE] keyword_importance column already exists")
    
    conn.close()
    print("[MIGRATE] Migration complete!")

if __name__ == "__main__":
    migrate()
