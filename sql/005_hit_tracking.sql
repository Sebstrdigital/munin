-- Migration: 005_hit_tracking.sql
-- US-003: Track recall hits on thoughts.
-- Adds hit_count, last_hit_at, and superseded_by columns.
-- Idempotent: safe to re-run.

-- ---------------------------------------------------------------------------
-- New columns
-- ---------------------------------------------------------------------------

ALTER TABLE thoughts
    ADD COLUMN IF NOT EXISTS hit_count    INT         NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_hit_at  TIMESTAMPTZ          DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS superseded_by UUID                DEFAULT NULL
        REFERENCES thoughts (id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- Index: fast lookup of cold records (hit_count = 0) for future compaction
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_thoughts_cold
    ON thoughts (last_hit_at)
    WHERE hit_count = 0;
