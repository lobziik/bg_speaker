-- Performance indexes for narration log
CREATE INDEX IF NOT EXISTS idx_narration_log_created ON narration_log(created_at);
CREATE INDEX IF NOT EXISTS idx_narration_log_user ON narration_log(user);
CREATE INDEX IF NOT EXISTS idx_narration_log_status ON narration_log(status);

-- Provider settings index
CREATE INDEX IF NOT EXISTS idx_provider_settings_active ON provider_settings(provider_type, is_active);
