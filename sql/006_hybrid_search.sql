-- Migration: 006_hybrid_search.sql
-- Adds a stored tsvector generated column and GIN index for hybrid (lexical+vector) search.
-- Idempotent: uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS guards.

-- ---------------------------------------------------------------------------
-- Add generated tsvector column (STORED — auto-populated from content on write)
-- ---------------------------------------------------------------------------
-- GENERATED ALWAYS AS ... STORED computes the value at write time and persists it.
-- Postgres backfills all existing rows automatically when the column is added.
-- No trigger needed; the value stays in sync on every INSERT/UPDATE.
ALTER TABLE thoughts
    ADD COLUMN IF NOT EXISTS content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

-- ---------------------------------------------------------------------------
-- GIN index for fast full-text search over the tsvector column
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_thoughts_content_tsv
    ON thoughts
    USING gin (content_tsv);
