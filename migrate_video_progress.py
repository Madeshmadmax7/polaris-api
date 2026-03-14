"""
Database Migration Script for Video Progress Tracking
Adds new columns to chapter_progress table for video duration and watch progress tracking.
"""

import sqlite3
import sys
from pathlib import Path

def migrate_database(db_path: str = "lifeos.db"):
    """Add video progress tracking columns to chapter_progress table."""
    
    print(f"[Migration] Starting database migration for {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(chapter_progress)")
        columns = [col[1] for col in cursor.fetchall()]
        
        print(f"[Migration] Existing columns: {columns}")
        
        # Add new columns if they don't exist
        if 'video_duration_seconds' not in columns:
            print("[Migration] Adding column: video_duration_seconds")
            cursor.execute("""
                ALTER TABLE chapter_progress 
                ADD COLUMN video_duration_seconds INTEGER DEFAULT 0
            """)
            print("[Migration] ✓ Added video_duration_seconds")
        else:
            print("[Migration] Column video_duration_seconds already exists")
        
        if 'watched_seconds' not in columns:
            print("[Migration] Adding column: watched_seconds")
            cursor.execute("""
                ALTER TABLE chapter_progress 
                ADD COLUMN watched_seconds INTEGER DEFAULT 0
            """)
            print("[Migration] ✓ Added watched_seconds")
        else:
            print("[Migration] Column watched_seconds already exists")
        
        if 'creator_name' not in columns:
            print("[Migration] Adding column: creator_name")
            cursor.execute("""
                ALTER TABLE chapter_progress 
                ADD COLUMN creator_name VARCHAR(100)
            """)
            print("[Migration] ✓ Added creator_name")
        else:
            print("[Migration] Column creator_name already exists")
        
        conn.commit()
        print("[Migration] ✓ Migration completed successfully!")
        
    except Exception as e:
        print(f"[Migration Error] {str(e)}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    # Get database path from command line or use default
    db_path = sys.argv[1] if len(sys.argv) > 1 else "lifeos.db"
    
    # Check if database exists
    if not Path(db_path).exists():
        print(f"[Migration Error] Database not found: {db_path}")
        sys.exit(1)
    
    migrate_database(db_path)
