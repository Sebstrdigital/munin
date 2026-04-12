-- Migration: 002_fingerprint.sql
-- Backfills content_fingerprint and adds a unique partial index per project.
-- Idempotent: safe to re-run.

-- ---------------------------------------------------------------------------
-- Backfill: compute md5(content) for any rows missing a fingerprint
-- ---------------------------------------------------------------------------

UPDATE thoughts
SET content_fingerprint = md5(content)
WHERE content_fingerprint IS NULL;

-- ---------------------------------------------------------------------------
-- Unique partial index: (project, content_fingerprint) where fingerprint set
-- Partial so rows without a fingerprint (pre-migration or in-flight) are not
-- subject to the uniqueness constraint.
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS idx_thoughts_fingerprint
    ON thoughts (project, content_fingerprint)
    WHERE content_fingerprint IS NOT NULL;
