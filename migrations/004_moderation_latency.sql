-- Migration: Add moderation latency column to narration_log
-- Stores the time spent on content moderation check in milliseconds

ALTER TABLE narration_log ADD COLUMN latency_moderation_ms INTEGER;
