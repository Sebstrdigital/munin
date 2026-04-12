-- Migration: 001_schema.sql
-- Creates the thoughts table, supporting indexes, and updated_at trigger.
-- Idempotent: safe to apply against a database where the extension is already enabled.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS thoughts (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    content             TEXT        NOT NULL,
    embedding           VECTOR(768) NOT NULL,
    project             TEXT        NOT NULL,
    scope               TEXT,
    tags                TEXT[]      NOT NULL DEFAULT '{}',
    metadata            JSONB       NOT NULL DEFAULT '{}',
    content_fingerprint TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Btree index for project/scope filtering (used in match_thoughts WHERE clause)
CREATE INDEX IF NOT EXISTS idx_thoughts_project_scope
    ON thoughts (project, scope);

-- HNSW index for cosine similarity search
-- m=16, ef_construction=64 are pgvector defaults; listed explicitly for clarity.
CREATE INDEX IF NOT EXISTS idx_thoughts_embedding_hnsw
    ON thoughts
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- updated_at trigger
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Drop + recreate guard so the migration is re-runnable without error.
DROP TRIGGER IF EXISTS trg_thoughts_set_updated_at ON thoughts;

CREATE TRIGGER trg_thoughts_set_updated_at
    BEFORE UPDATE ON thoughts
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
