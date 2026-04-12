-- Migration: 003_match_thoughts.sql
-- Creates the match_thoughts() RPC for filtered vector similarity search.
-- Idempotent: CREATE OR REPLACE is safe to re-apply.

CREATE OR REPLACE FUNCTION match_thoughts(
    query_embedding       vector(768),
    project               text,
    scope                 text    DEFAULT NULL,
    match_limit           int     DEFAULT 10,
    similarity_threshold  float   DEFAULT 0.0
)
RETURNS TABLE (
    id          uuid,
    content     text,
    project     text,
    scope       text,
    tags        text[],
    metadata    jsonb,
    similarity  float,
    created_at  timestamptz,
    updated_at  timestamptz
)
LANGUAGE sql STABLE AS $$
    SELECT
        t.id,
        t.content,
        t.project,
        t.scope,
        t.tags,
        t.metadata,
        1 - (t.embedding <=> query_embedding) AS similarity,
        t.created_at,
        t.updated_at
    FROM thoughts t
    WHERE
        t.project = match_thoughts.project
        AND (match_thoughts.scope IS NULL OR t.scope = match_thoughts.scope)
        AND (1 - (t.embedding <=> query_embedding)) >= similarity_threshold
    ORDER BY t.embedding <=> query_embedding
    LIMIT match_limit;
$$;
