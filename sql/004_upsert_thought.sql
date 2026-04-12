-- US-004: upsert_thought() RPC with dedup-aware insert
-- Computes content_fingerprint internally; callers never set it.
-- ON CONFLICT matches the partial unique index from US-002:
--   (project, content_fingerprint) WHERE content_fingerprint IS NOT NULL

CREATE OR REPLACE FUNCTION upsert_thought(
    p_content    text,
    p_embedding  vector(768),
    p_project    text,
    p_scope      text      DEFAULT NULL,
    p_tags       text[]    DEFAULT '{}',
    p_metadata   jsonb     DEFAULT '{}'::jsonb
) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE
    v_fingerprint text;
    v_id          uuid;
BEGIN
    v_fingerprint := md5(p_content);

    INSERT INTO thoughts (content, embedding, project, scope, tags, metadata, content_fingerprint)
    VALUES (p_content, p_embedding, p_project, p_scope, p_tags, p_metadata, v_fingerprint)
    ON CONFLICT (project, content_fingerprint) WHERE content_fingerprint IS NOT NULL
    DO UPDATE SET
        tags       = EXCLUDED.tags,
        metadata   = EXCLUDED.metadata,
        updated_at = now()
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;
