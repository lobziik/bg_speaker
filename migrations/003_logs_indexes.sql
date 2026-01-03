-- Additional indexes for efficient log queries
CREATE INDEX IF NOT EXISTS idx_narration_log_created_desc
    ON narration_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_narration_log_user_created
    ON narration_log(user, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_narration_log_status_created
    ON narration_log(status, created_at DESC);

-- Composite index for filtered queries
CREATE INDEX IF NOT EXISTS idx_narration_log_filter
    ON narration_log(status, user, created_at DESC);

-- Index for date-based aggregation
CREATE INDEX IF NOT EXISTS idx_narration_log_date
    ON narration_log(DATE(created_at));
