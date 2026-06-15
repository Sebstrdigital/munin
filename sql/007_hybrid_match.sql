-- Migration: 007_hybrid_match.sql
-- US-003: Rewrite match_thoughts with hybrid RRF + multi-signal ranking + ef tuning.
--
-- Strategy:
--   1. Dense leg   — top-N candidates by cosine similarity (HNSW, ef_search=100).
--   2. Lexical leg — top-N candidates by full-text rank (ts_rank over content_tsv).
--   3. Fuse both legs via Reciprocal Rank Fusion: rrf_score = Σ 1/(k + rank_i).
--   4. Final score = w_rrf * rrf_score
--                  + w_recency * recency_signal
--                  + w_hits * hits_signal
--   where recency_signal and hits_signal are normalised to [0,1].
--
-- Parameters (all have documented defaults):
--   query_embedding      vector(768)   — dense query vector
--   query_text           text          — raw query string for lexical leg
--   p_project            text          — mandatory project filter
--   p_scope              text          — optional scope filter (NULL = all scopes)
--   match_limit          int  = 10     — number of rows to return
--   similarity_threshold float = 0.0  — minimum cosine similarity to enter dense leg
--   rrf_k                int  = 60    — RRF constant (larger → less aggressive re-ranking)
--   candidate_factor     int  = 5     — how many candidates each leg fetches relative to match_limit
--   w_rrf                float = 0.7  — weight for fused relevance signal
--   w_recency            float = 0.2  — weight for recency signal (last_hit_at / created_at)
--   w_hits               float = 0.1  — weight for normalised hit_count signal
--
-- NOTE: Parameters are prefixed p_ to avoid collision with the identically-named
-- output columns in the RETURNS TABLE clause (Postgres name-resolution conflict
-- inside PL/pgSQL). The caller (memory.py) passes positionally so the rename is
-- invisible to callers.
--
-- NOTE: SET LOCAL hnsw.ef_search / hnsw.iterative_scan cannot be issued inside a
-- STABLE function (Postgres forbids SET in non-volatile functions). These settings
-- are instead issued by the Python caller (memory.py) via SET LOCAL within the
-- same transaction before calling match_thoughts(). The function itself stays STABLE.
--
-- Signature change: new parameters added.  The old 5-parameter signature no longer
-- exists after this migration, so we DROP it first to avoid an overload conflict.
-- ---------------------------------------------------------------------------

-- Drop old 5-parameter signature (vector, text, text, int, float).
DROP FUNCTION IF EXISTS match_thoughts(
    vector, text, text, int, float
);

-- Also drop the 11-parameter signature if it already exists. Re-applying this
-- migration onto a database whose match_thoughts already returns a different
-- set of columns (e.g. one already at migration 009, which adds a `score`
-- column) would otherwise fail with "cannot change return type of existing
-- function" under CREATE OR REPLACE. Dropping first makes the whole 006-009
-- migration chain safely re-runnable from any prior function shape.
DROP FUNCTION IF EXISTS match_thoughts(
    vector, text, text, text, int, float, int, int, float, float, float
);

-- ---------------------------------------------------------------------------
-- Hybrid match_thoughts
-- ---------------------------------------------------------------------------
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
    -- -----------------------------------------------------------------------
    stats AS (
        SELECT
            MAX(f.rrf_score)                       AS max_rrf,
            EXTRACT(EPOCH FROM MAX(t.last_hit_at)) AS max_ts,
            EXTRACT(EPOCH FROM MIN(t.created_at))  AS min_ts,
            MAX(t.hit_count)                       AS max_hits
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
