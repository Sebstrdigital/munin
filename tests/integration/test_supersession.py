"""Integration tests for P2-2: supersession / conflict handling.

Requires postgres on localhost:5433 and embed server on localhost:8088.
Run: MUNIN_PG_DB=munin_test pytest tests/integration/test_supersession.py -v
"""
from __future__ import annotations

from munin.core.config import MuninConfig
from munin.core.memory import recall, remember, show

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _cfg_supersede_on(base: MuninConfig) -> MuninConfig:
    """Return a config with supersession enabled at a low threshold (0.3)
    so even moderately similar thoughts trigger supersession in tests."""
    return MuninConfig(
        db_url=base.db_url,
        embed_url=base.embed_url,
        embed_dim=base.embed_dim,
        default_limit=base.default_limit,
        embed_batch_size=base.embed_batch_size,
        # Dedup at high threshold (0.9999) so we never hit the exact-dup path
        # during these tests — we want the supersession path, not the skip path.
        remember_dedup_enabled=True,
        remember_dedup_threshold=0.9999,
        remember_supersede_enabled=True,
        remember_supersede_threshold=0.30,
    )


def _cfg_supersede_off(base: MuninConfig) -> MuninConfig:
    """Return a config with supersession disabled."""
    return MuninConfig(
        db_url=base.db_url,
        embed_url=base.embed_url,
        embed_dim=base.embed_dim,
        default_limit=base.default_limit,
        embed_batch_size=base.embed_batch_size,
        remember_dedup_enabled=False,
        remember_supersede_enabled=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSupersession:
    """P2-2 acceptance criteria."""

    def test_updating_decision_retires_old_from_default_recall(
        self, cfg: MuninConfig
    ) -> None:
        """(a) Updating a decision retires the old row from default recall.

        Store an old architectural decision, then store a conflicting new one.
        With supersession on and a low threshold (0.30), the old row should be
        marked superseded_by = new_id and excluded from match_thoughts output.
        """
        scfg = _cfg_supersede_on(cfg)
        proj = "pytest_supersession"

        old_id = remember(
            "We use PostgreSQL as the primary database",
            project=proj,
            config=scfg,
        )

        new_id = remember(
            "We use SQLite as the primary database",
            project=proj,
            config=scfg,
        )

        # They must be distinct rows.
        assert old_id != new_id

        # Default recall must NOT return the old (superseded) thought.
        results = recall("primary database", project=proj, config=scfg)
        returned_ids = {r.id for r in results}
        assert old_id not in returned_ids, (
            f"Superseded thought {old_id} appeared in default recall"
        )
        # The new thought should appear.
        assert new_id in returned_ids, (
            f"New thought {new_id} missing from recall results"
        )

    def test_retired_row_fetchable_by_id(self, cfg: MuninConfig) -> None:
        """(b) The retired row is still fetchable via show(id)."""
        scfg = _cfg_supersede_on(cfg)
        proj = "pytest_supersession"

        old_id = remember(
            "The cache layer uses Redis",
            project=proj,
            config=scfg,
        )
        _new_id = remember(
            "The cache layer uses Memcached",
            project=proj,
            config=scfg,
        )

        # show() bypasses the superseded_by filter — must return the old thought.
        thought = show(old_id, config=scfg)
        assert thought is not None, f"Retired thought {old_id} not fetchable by id"
        assert thought.id == old_id
        assert "Redis" in thought.content

    def test_retired_row_has_superseded_by_set(self, cfg: MuninConfig) -> None:
        """(b-ext) The retired row has superseded_by pointing to the new thought."""
        from munin.core.db import get_pool

        scfg = _cfg_supersede_on(cfg)
        proj = "pytest_supersession"

        old_id = remember(
            "Authentication uses JWT tokens",
            project=proj,
            config=scfg,
        )
        new_id = remember(
            "Authentication uses session cookies",
            project=proj,
            config=scfg,
        )

        pool = get_pool(scfg)
        pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT superseded_by FROM thoughts WHERE id = %s",
                    (old_id,),
                )
                row = cur.fetchone()

        assert row is not None
        assert row[0] == new_id, (
            f"Expected superseded_by={new_id}, got {row[0]}"
        )

    def test_flag_off_no_supersession(self, cfg: MuninConfig) -> None:
        """(c) With remember_supersede_enabled=False, no row is retired."""
        from munin.core.db import get_pool

        scfg = _cfg_supersede_off(cfg)
        proj = "pytest_supersession"

        old_id = remember(
            "Logging uses structured JSON format",
            project=proj,
            config=scfg,
        )
        _new_id = remember(
            "Logging uses plain text format",
            project=proj,
            config=scfg,
        )

        pool = get_pool(scfg)
        pool.open(wait=True)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT superseded_by FROM thoughts WHERE id = %s",
                    (old_id,),
                )
                row = cur.fetchone()

        assert row is not None
        assert row[0] is None, (
            f"superseded_by should be NULL when flag is off, got {row[0]}"
        )

    def test_row_count_preserved(self, cfg: MuninConfig) -> None:
        """Migration must preserve row count — no rows lost on supersession."""
        from munin.core.db import get_pool

        scfg = _cfg_supersede_on(cfg)
        proj = "pytest_supersession"

        pool = get_pool(scfg)
        pool.open(wait=True)

        def _count() -> int:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM thoughts WHERE project = %s", (proj,)
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else 0

        before = _count()
        _old = remember(
            "Deployment targets AWS ECS",
            project=proj,
            config=scfg,
        )
        _new = remember(
            "Deployment targets Google Cloud Run",
            project=proj,
            config=scfg,
        )
        after = _count()

        # Both rows must exist (supersession is a soft update, never a delete).
        assert after == before + 2, (
            f"Expected {before + 2} rows, got {after} — supersession must not delete"
        )
