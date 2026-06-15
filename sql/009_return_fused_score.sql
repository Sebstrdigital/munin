-- ---------------------------------------------------------------------------
-- Migration 009: Return fused multi-signal score from match_thoughts()
--
-- Problem: match_thoughts() previously returned only `similarity` (raw cosine
-- similarity, s_cosine_sim).  The multi-signal final score (s_final_score —
-- weighted blend of RRF rank, recency, and hit_count) was used only for
-- ORDER BY and was never surfaced to callers.  As a result, MMR re-ranking
-- used pure cosine as its relevance term, discarding the fused signal.
--
-- Fix: Add `score double precision` to the RETURNS TABLE.  The existing
-- `similarity` column retains its meaning (cosine, for display/threshold).
-- The new `score` column carries the fused final score for MMR relevance.
--
-- Postgres rejects changing RETURNS TABLE columns via CREATE OR REPLACE when
-- the existing function has a different return type.  We therefore DROP the
-- 11-arg signature first, then CREATE the updated function.
--
-- The function logic (all 11 input parameters and the scoring CTE) is
-- identical to 008; only the RETURNS TABLE and final SELECT are changed.
-- ---------------------------------------------------------------------------

DROP FUNCTION IF EXISTS match_thoughts(
    vector(768),
    text,
    text,
    text,
    int,
    float,
    int,
    int,
    float,
    float,
    float
);

CREATE FUNCTION match_thoughts(
    query_embedding       vector(768),
    query_text            text,
    p_project             text,
    p_scope               text    DEFAULT NULL,
    match_limit           int     DEFAULT 10,
    similarity_threshold  float   DEFAULT 0.0,
    rrf_k                 int     DEFAULT 60,
    candidate_factor      int     DEFAULT 5,
    w_rrf                 float   DEFAULT 0.7,
    w_recency             float   DEFAULT 0.2,
    w_hits                float   DEFAULT 0.1
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
    updated_at  timestamptz,
    score       double precision
)
-- hnsw.ef_search and hnsw.iterative_scan are set by the Python caller via
-- SET LOCAL within the same transaction before invoking this function.
-- This keeps the function STABLE (SET is not permitted inside STABLE functions).
LANGUAGE plpgsql STABLE AS $$
DECLARE
    candidate_limit int := match_limit * candidate_factor;
BEGIN
    RETURN QUERY
    WITH
    -- -----------------------------------------------------------------------
    -- Dense leg: top-N by cosine similarity, project/scope filtered
    -- -----------------------------------------------------------------------
    dense AS (
        SELECT
            t.id,
            ROW_NUMBER() OVER (ORDER BY t.embedding <=> query_embedding) AS rank
        FROM thoughts t
        WHERE
            t.project = p_project
            AND (p_scope IS NULL OR t.scope = p_scope)
            AND (1 - (t.embedding <=> query_embedding)) >= similarity_threshold
        ORDER BY t.embedding <=> query_embedding
        LIMIT candidate_limit
    ),

    -- -----------------------------------------------------------------------
    -- Lexical leg: top-N by ts_rank, project/scope filtered.
    -- Only runs when query_text is non-empty.
    -- -----------------------------------------------------------------------
    lexical AS (
        SELECT
            t.id,
            ROW_NUMBER() OVER (
                ORDER BY ts_rank(t.content_tsv, websearch_to_tsquery('english', query_text)) DESC
            ) AS rank
        FROM thoughts t
        WHERE
            t.project = p_project
            AND (p_scope IS NULL OR t.scope = p_scope)
            AND query_text IS NOT NULL
            AND query_text <> ''
            AND t.content_tsv @@ websearch_to_tsquery('english', query_text)
        ORDER BY ts_rank(t.content_tsv, websearch_to_tsquery('english', query_text)) DESC
        LIMIT candidate_limit
    ),

    -- -----------------------------------------------------------------------
    -- RRF fusion: merge both legs, compute reciprocal rank sum per thought
    -- -----------------------------------------------------------------------
    fused AS (
        SELECT
            COALESCE(d.id, l.id) AS thought_id,
            COALESCE(1.0 / (rrf_k + d.rank), 0.0)
            + COALESCE(1.0 / (rrf_k + l.rank), 0.0) AS rrf_score
        FROM dense d
        FULL OUTER JOIN lexical l ON d.id = l.id
    ),

    -- -----------------------------------------------------------------------
    -- Normalisation stats over the fused candidate set
    -- Fix: max_ts and min_ts now use the same COALESCE(last_hit_at, created_at)
    -- expression as the per-row recency numerator, ensuring [0,1] bounds.
    -- -----------------------------------------------------------------------
    stats AS (
        SELECT
            MAX(f.rrf_score)                                                   AS max_rrf,
            EXTRACT(EPOCH FROM MAX(COALESCE(t.last_hit_at, t.created_at)))     AS max_ts,
            EXTRACT(EPOCH FROM MIN(COALESCE(t.last_hit_at, t.created_at)))     AS min_ts,
            MAX(t.hit_count)                                                   AS max_hits
        FROM fused f
        JOIN thoughts t ON t.id = f.thought_id
    ),

    -- -----------------------------------------------------------------------
    -- Final scoring: weighted combination of fused relevance + signals
    -- -----------------------------------------------------------------------
    scored AS (
        SELECT
            t.id                                          AS s_id,
            t.content                                     AS s_content,
            t.project                                     AS s_project,
            t.scope                                       AS s_scope,
            t.tags                                        AS s_tags,
            t.metadata                                    AS s_metadata,
            t.created_at                                  AS s_created_at,
            t.updated_at                                  AS s_updated_at,
            -- Cosine similarity for the output column (backward-compatible display)
            (1 - (t.embedding <=> query_embedding))::float AS s_cosine_sim,
            -- Multi-signal final score (fused relevance + recency + hits)
            (
                w_rrf * (
                    CASE WHEN s.max_rrf > 0
                    THEN f.rrf_score / s.max_rrf
                    ELSE 0.0 END
                )
                + w_recency * (
                    CASE
                        WHEN s.max_ts IS NULL OR s.max_ts = s.min_ts THEN 0.0
                        ELSE (
                            EXTRACT(EPOCH FROM COALESCE(t.last_hit_at, t.created_at))
                            - s.min_ts
                        ) / NULLIF(s.max_ts - s.min_ts, 0)
                    END
                )
                + w_hits * (
                    CASE WHEN s.max_hits > 0
                    THEN t.hit_count::float / s.max_hits
                    ELSE 0.0 END
                )
            )::double precision                           AS s_final_score
        FROM fused f
        JOIN thoughts t ON t.id = f.thought_id
        CROSS JOIN stats s
    )

    SELECT
        s.s_id,
        s.s_content,
        s.s_project,
        s.s_scope,
        s.s_tags,
        s.s_metadata,
        s.s_cosine_sim,
        s.s_created_at,
        s.s_updated_at,
        s.s_final_score
    FROM scored s
    ORDER BY s.s_final_score DESC
    LIMIT match_limit;
END;
$$;
