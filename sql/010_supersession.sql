-- ---------------------------------------------------------------------------
-- Migration 010: Supersession — exclude superseded rows from default recall
--
-- Background: sql/005 added the superseded_by column (UUID FK) as unused
-- scaffolding.  P2-2 wires it up: when a new thought conflicts with an older
-- near-duplicate, the old row gets superseded_by = new.id, and match_thoughts
-- should exclude those rows from default recall so callers no longer see both
-- the old and the new contradictory thoughts.
--
-- Change: add WHERE superseded_by IS NULL to both the dense and the lexical
-- legs of match_thoughts.  Thoughts with a non-NULL superseded_by are retired
-- from default retrieval but remain physically present and are still reachable
-- via direct id lookup (show) or when callers query without this filter.
--
-- Postgres cannot use CREATE OR REPLACE when the RETURNS TABLE set changes,
-- and we must stay re-runnable from any prior function shape in the 006→010
-- chain.  We therefore DROP the 11-arg signature first (same pattern as 009).
-- Function logic and return columns are identical to 009; only the two dense
-- and lexical WHERE clauses gain the extra superseded_by IS NULL predicate.
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
    -- Dense leg: top-N by cosine similarity, project/scope filtered.
    -- P2-2: superseded rows excluded from default recall.
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
            AND t.superseded_by IS NULL
        ORDER BY t.embedding <=> query_embedding
        LIMIT candidate_limit
    ),

    -- -----------------------------------------------------------------------
    -- Lexical leg: top-N by ts_rank, project/scope filtered.
    -- P2-2: superseded rows excluded from default recall.
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
            AND t.superseded_by IS NULL
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

-- ---------------------------------------------------------------------------
-- Index: fast exclusion of superseded rows in match_thoughts WHERE clauses
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_thoughts_not_superseded
    ON thoughts (project)
    WHERE superseded_by IS NULL;
