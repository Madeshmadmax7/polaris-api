"""
Database Migration Script (SQLite Compatible)
Updates schema for the new unified learning flow.
"""

from sqlalchemy import create_engine, text, inspect
from app.config.settings import settings

def migrate():
    engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"ssl": {"ssl_disabled": False}}
)
    inspector = inspect(engine)
    
    with engine.connect() as conn:
        print("[Migration] Starting database schema migration...")
        
        # 1. Drop DocumentChunk table if exists
        tables = inspector.get_table_names()
        if 'document_chunks' in tables:
            try:
                conn.execute(text("DROP TABLE document_chunks"))
                conn.commit()
                print("✓ Dropped document_chunks table")
            except Exception as e:
                print(f"  ⚠ Could not drop document_chunks: {e}")
        
        # 2. Check documents table columns
        doc_columns = [col['name'] for col in inspector.get_columns('documents')]
        
        # Add content column if missing
        if 'content' not in doc_columns:
            try:
                conn.execute(text("ALTER TABLE documents ADD COLUMN content TEXT"))
                conn.commit()
                print("✓ Added content column to documents")
            except Exception as e:
                print(f"  ⚠ Could not add content column: {e}")
        
        # SQLite doesn't support DROP COLUMN easily, so we'll recreate the table
        if 'chunk_count' in doc_columns or 'faiss_index_path' in doc_columns:
            print("  → Recreating documents table without FAISS columns...")
            try:
                # Create new table
                conn.execute(text("""
                    CREATE TABLE documents_new (
                        id VARCHAR(36) PRIMARY KEY,
                        user_id VARCHAR(36) NOT NULL,
                        filename VARCHAR(255) NOT NULL,
                        file_type VARCHAR(50) DEFAULT 'pdf',
                        content TEXT,
                        created_at DATETIME NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                """))
                
                # Copy data
                conn.execute(text("""
                    INSERT INTO documents_new (id, user_id, filename, file_type, created_at)
                    SELECT id, user_id, filename, file_type, created_at FROM documents
                """))
                
                # Drop old, rename new
                conn.execute(text("DROP TABLE documents"))
                conn.execute(text("ALTER TABLE documents_new RENAME TO documents"))
                
                # Recreate index
                conn.execute(text("CREATE INDEX idx_documents_user ON documents(user_id)"))
                
                conn.commit()
                print("✓ Recreated documents table without FAISS columns")
            except Exception as e:
                print(f"  ⚠ Could not recreate documents table: {e}")
                conn.rollback()
        
        # 3. Add quiz_unlocked to study_plans
        plan_columns = [col['name'] for col in inspector.get_columns('study_plans')]
        if 'quiz_unlocked' not in plan_columns:
            try:
                conn.execute(text("ALTER TABLE study_plans ADD COLUMN quiz_unlocked BOOLEAN DEFAULT 0"))
                conn.commit()
                print("✓ Added quiz_unlocked column to study_plans")
            except Exception as e:
                print(f"  ⚠ Could not add quiz_unlocked: {e}")
        
        # 4. Create chapter_progress table
        if 'chapter_progress' not in tables:
            try:
                conn.execute(text("""
                    CREATE TABLE chapter_progress (
                        id VARCHAR(36) PRIMARY KEY,
                        study_plan_id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        chapter_index INTEGER NOT NULL,
                        chapter_title VARCHAR(255) NOT NULL,
                        youtube_url VARCHAR(500),
                        is_completed BOOLEAN DEFAULT 0,
                        completed_at DATETIME,
                        created_at DATETIME NOT NULL,
                        FOREIGN KEY (study_plan_id) REFERENCES study_plans(id) ON DELETE CASCADE,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                """))
                conn.commit()
                print("✓ Created chapter_progress table")
            except Exception as e:
                print(f"  ⚠ Could not create chapter_progress: {e}")
        
        # 5. Create index
        try:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_chapter_progress 
                ON chapter_progress(study_plan_id, chapter_index)
            """))
            conn.commit()
            print("✓ Created index on chapter_progress")
        except Exception as e:
            print(f"  ⚠ Index might already exist: {e}")
        
        print("\n[Migration] ✅ Database migration completed successfully!")
        print("\nNext steps:")
        print("1. Restart the backend server")
        print("2. Upload PDFs - they'll now extract text only (fast!)")
        print("3. Create study plans - they'll include YouTube chapters + quiz")
        print("4. Complete chapters to unlock the quiz")


if __name__ == "__main__":
    migrate()
