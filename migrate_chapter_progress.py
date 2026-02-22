"""
Migration script to add auto-assignment tracking fields to chapter_progress table.
Adds: assigned_video_id, assigned_video_title, assigned_duration_seconds, watched_seconds
"""
import sqlite3

def migrate():
    conn = sqlite3.connect('./lifeos.db')
    cursor = conn.cursor()
    
    try:
        # Add new columns for auto-assignment tracking
        print("Adding assigned_video_id column...")
        cursor.execute("""
            ALTER TABLE chapter_progress 
            ADD COLUMN assigned_video_id VARCHAR(50)
        """)
        
        print("Adding assigned_video_title column...")
        cursor.execute("""
            ALTER TABLE chapter_progress 
            ADD COLUMN assigned_video_title VARCHAR(500)
        """)
        
        print("Adding assigned_duration_seconds column...")
        cursor.execute("""
            ALTER TABLE chapter_progress 
            ADD COLUMN assigned_duration_seconds INTEGER
        """)
        
        print("Adding watched_seconds column...")
        cursor.execute("""
            ALTER TABLE chapter_progress 
            ADD COLUMN watched_seconds INTEGER DEFAULT 0
        """)
        
        # Update existing rows to have watched_seconds = 0
        cursor.execute("""
            UPDATE chapter_progress 
            SET watched_seconds = 0 
            WHERE watched_seconds IS NULL
        """)
        
        conn.commit()
        print("✅ Migration completed successfully!")
        
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("⚠️ Columns already exist, skipping migration.")
        else:
            print(f"❌ Migration failed: {e}")
            conn.rollback()
            raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
