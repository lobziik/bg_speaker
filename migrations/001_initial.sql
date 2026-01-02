-- Core settings table
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Provider settings (LLM, TTS, Translation)
CREATE TABLE IF NOT EXISTS provider_settings (
    provider_type TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    settings TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_type, provider_name)
);

-- Twitch OAuth state
CREATE TABLE IF NOT EXISTS twitch_state (
    key TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    broadcaster_id TEXT NOT NULL,
    broadcaster_login TEXT NOT NULL,
    reward_id TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Narration history log
CREATE TABLE IF NOT EXISTS narration_log (
    id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    message_original TEXT NOT NULL,
    text_formatted TEXT,
    text_translated TEXT,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    was_translated BOOLEAN DEFAULT FALSE,
    llm_provider TEXT,
    tts_provider TEXT,
    latency_llm_ms INTEGER,
    latency_tts_ms INTEGER,
    latency_total_ms INTEGER,
    queue_wait_ms INTEGER,
    status TEXT NOT NULL,
    rejection_reason TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Banned users
CREATE TABLE IF NOT EXISTS banned_users (
    username TEXT PRIMARY KEY,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Banned words
CREATE TABLE IF NOT EXISTS banned_words (
    word TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
