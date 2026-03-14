-- Parent-Child Connection System Database Schema
-- Run this to create the parent_child_connections table if auto-migration doesn't work

CREATE TABLE IF NOT EXISTS parent_child_connections (
    id VARCHAR(36) PRIMARY KEY,
    parent_id VARCHAR(36) NOT NULL,
    child_id VARCHAR(36) NOT NULL,
    otp_code VARCHAR(4) NOT NULL,
    otp_created_at DATETIME NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    connected_at DATETIME,
    expires_at DATETIME,
    status VARCHAR(20) DEFAULT 'pending' COMMENT "'pending' | 'active' | 'expired'",
    created_at DATETIME,
    
    FOREIGN KEY (parent_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (child_id) REFERENCES users(id) ON DELETE CASCADE,
    
    INDEX idx_parent_child_status (parent_id, child_id, status),
    INDEX idx_parent_child_connection (parent_id, child_id),
    INDEX idx_status (status),
    INDEX idx_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Note: The FastAPI auto-migration system will also create this table at startup
-- if it doesn't exist. See backend/app/main.py lifespan for auto-migration logic.
