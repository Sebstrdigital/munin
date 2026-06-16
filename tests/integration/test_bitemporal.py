"""Integration tests for P2-3: bi-temporal validity (valid_from / valid_to).

Requires postgres on localhost:5433 and embed server on localhost:8088.
Run: MUNIN_PG_DB=munin_test pytest tests/integration/test_bitemporal.py -v
"""
from __future__ import annotations

from munin.core.config import MuninConfig
from munin.core.db import get_pool
from munin.core.memory import recall, remember

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _cfg_supersede_on(base: MuninConfig) -> MuninConfig:
    """Config with supersession at a low threshold so tests reliably trigger it."""
    return MuninConfig(
        db_url=base.db_url,
        embed_url=base.embed_url,
        embed_dim=base.embed_dim,
        default_limit=base.default_limit,
        embed_batch_size=base.embed_batch_size,
        # Dedup at 0.9999 so we never short-circuit to the exact-dup path.
        remember_dedup_enabled=True,
        remember_dedup_threshold=0.9999,
        remember_supersede_enabled=True,
        remember_supersede_threshold=0.30,
    )


def _cfg_history_on(base: MuninConfig) -> MuninConfig:
    """Same as supersede_on but also sets recall_include_history=True."""
    cfg = _cfg_supersede_on(base)
    return MuninConfig(
        db_url=cfg.db_url,
        embed_url=cfg.embed_url,
        embed_dim=cfg.embed_dim,
        default_limit=cfg.default_limit,
        embed_batch_size=cfg.embed_batch_size,
        remember_dedup_enabled=cfg.remember_dedup_enabled,
        remember_dedup_threshold=cfg.remember_dedup_threshold,
        remember_supersede_enabled=cfg.remember_supersede_enabled,
        remember_supersede_threshold=cfg.remember_supersede_threshold,
        recall_include_history=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBitemporal:
    """P2-3 acceptance criteria."""

    def test_superseded_thought_has_valid_to_set(self, cfg: MuninConfig) -> None:
        """(a) When a thought is superseded, its valid_to is stamped with now()."""
        scfg = _cfg_supersede_on(cfg)
        proj = "pytest_bitemporal"

        old_id = remember(
            "We store sessions in Redis with a 24-hour TTL",
            project=proj,
            config=scfg,
        )
        _new_id = remember(
            "We store sessions in Redis with a 48-hour TTL",
            project=proj,
            config=scfg,
        )

        pool = get_pool(scfg)
        pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT valid_to FROM thoughts WHERE id = %s",
                    (old_id,),
                )
                row = cur.fetchone()

        assert row is not None
        assert row[0] is not None, (
            f"Expected valid_to to be set on superseded thought {old_id}, got NULL"
        )

    def test_superseded_thought_excluded_from_default_recall(
        self, cfg: MuninConfig
    ) -> None:
        """(a-cont) Default recall must NOT return a thought with valid_to set."""
        scfg = _cfg_supersede_on(cfg)
        proj = "pytest_bitemporal"

        old_id = remember(
            "The API rate limit is 100 requests per minute",
            project=proj,
            config=scfg,
        )
        new_id = remember(
            "The API rate limit is 500 requests per minute",
            project=proj,
            config=scfg,
        )

        results = recall("API rate limit", project=proj, config=scfg)
        returned_ids = {r.id for r in results}

        assert old_id not in returned_ids, (
            f"Expired thought {old_id} (valid_to set) appeared in default recall"
        )
        assert new_id in returned_ids, (
            f"Current thought {new_id} missing from default recall"
        )

    def test_history_mode_includes_expired_thought(self, cfg: MuninConfig) -> None:
        """(b) History mode returns rows with valid_to set alongside live rows."""
        scfg = _cfg_supersede_on(cfg)
        hist_cfg = _cfg_history_on(cfg)
        proj = "pytest_bitemporal"

        old_id = remember(
            "Database backups run daily at midnight",
            project=proj,
            config=scfg,
        )
        new_id = remember(
            "Database backups run hourly",
            project=proj,
            config=scfg,
        )

        # Default recall excludes the old (expired) thought.
        default_results = recall("database backups schedule", project=proj, config=scfg)
        default_ids = {r.id for r in default_results}
        assert old_id not in default_ids, (
            "Expired thought appeared in default recall — prerequisite for history test"
        )

        # History mode includes it.
        history_results = recall(
            "database backups schedule",
            project=proj,
            config=hist_cfg,
        )
        history_ids = {r.id for r in history_results}
        assert old_id in history_ids, (
            f"Expired thought {old_id} missing from history-mode recall"
        )
        assert new_id in history_ids, (
            f"Current thought {new_id} missing from history-mode recall"
        )

    def test_history_mode_via_param_override(self, cfg: MuninConfig) -> None:
        """(b-ext) include_history=True param overrides the config flag."""
        scfg = _cfg_supersede_on(cfg)
        proj = "pytest_bitemporal"

        old_id = remember(
            "Logging level is INFO in production",
            project=proj,
            config=scfg,
        )
        _new_id = remember(
            "Logging level is WARNING in production",
            project=proj,
            config=scfg,
        )

        # Default config (history off) — expired row hidden.
        default_results = recall(
            "logging level production", project=proj, config=scfg
        )
        assert old_id not in {r.id for r in default_results}

        # Explicit param override — expired row visible.
        history_results = recall(
            "logging level production",
            project=proj,
            include_history=True,
            config=scfg,
        )
        assert old_id in {r.id for r in history_results}, (
            f"Expired thought {old_id} missing when include_history=True via param"
        )

    def test_fresh_thought_has_valid_from_set_and_valid_to_null(
        self, cfg: MuninConfig
    ) -> None:
        """(c) A fresh thought has valid_from set and valid_to NULL."""
        proj = "pytest_bitemporal"

        thought_id = remember(
            "Monitoring uses Prometheus with a 15s scrape interval",
            project=proj,
            config=cfg,
        )

        pool = get_pool(cfg)
        pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT valid_from, valid_to FROM thoughts WHERE id = %s",
                    (thought_id,),
                )
                row = cur.fetchone()

        assert row is not None
        assert row[0] is not None, (
            f"valid_from is NULL on fresh thought {thought_id} — should be set by default"
        )
        assert row[1] is None, (
            f"valid_to is NOT NULL on fresh thought {thought_id} — should be NULL"
        )

    def test_row_count_preserved_after_migration(self, cfg: MuninConfig) -> None:
        """Columns are additive only — zero rows lost."""
        pool = get_pool(cfg)
        pool.open(wait=True)

        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM thoughts")
                total = int(cur.fetchone()[0])  # type: ignore[index]
                cur.execute(
                    "SELECT COUNT(*) FROM thoughts WHERE valid_from IS NOT NULL"
                )
                with_valid_from = int(cur.fetchone()[0])  # type: ignore[index]

        assert total == with_valid_from, (
            f"valid_from is NULL on {total - with_valid_from} rows — "
            "migration should back-fill all existing rows via DEFAULT now()"
        )
