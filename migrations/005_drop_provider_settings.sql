-- Migration: Drop the unused provider_settings table
-- Provider selection lives in the settings table under the `providers` key
-- (ProviderSettings), written by the settings repository. This table was never
-- read or written by any code path; its index goes with it.

DROP TABLE IF EXISTS provider_settings;
