-- Migration: 008_fix_recency_norm.sql
-- Fix: recency normalization could exceed [0,1] when a never-hit thought's
-- created_at is newer than every row's last_hit_at.
--
-- Root cause in 007: stats CTE computed
--   max_ts = MAX(last_hit_at)   -- only considers rows WITH a last_hit_at
--   min_ts = MIN(created_at)    -- only considers created_at
-- but the per-row numerator uses COALESCE(last_hit_at, created_at).
-- A never-hit thought with a recent created_at produces a numerator greater
-- than max_ts, yielding recency_signal > 1.0.
--
-- Fix: compute max_ts and min_ts over the SAME expression used per-row:
--   max_ts = MAX(COALESCE(last_hit_at, created_at))
--   min_ts = MIN(COALESCE(last_hit_at, created_at))
-- This guarantees the per-row value is always within [min_ts, max_ts],
-- so recency_signal stays in [0,1].
--
-- Signature is identical to 007, so CREATE OR REPLACE would be sufficient —
-- but we add an explicit DROP first for chain re-runnability (guardrail
-- requirement: sql/009 already uses DROP-first; mirror the pattern here so
-- 008 can be re-applied standalone without error).
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

CREATE OR REPLACE FUNCTION match_thoughts(
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
    updated_at  timestamptz
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
            -- Cosine similarity for the output column
            (1 - (t.embedding <=> query_embedding))::float AS s_cosine_sim,
            -- Multi-signal final score
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
            ) AS s_final_score
        FROM fused f
        JOIN thoughts t  ON t.id = f.thought_id
        CROSS JOIN stats s
    )

    SELECT
        sc.s_id,
        sc.s_content,
        sc.s_project,
        sc.s_scope,
        sc.s_tags,
        sc.s_metadata,
        sc.s_cosine_sim,
        sc.s_created_at,
        sc.s_updated_at
    FROM scored sc
    ORDER BY sc.s_final_score DESC
    LIMIT match_limit;

END;
$$;
