"""Integration tests for core.memory against the live docker-compose stack.

Requires postgres on localhost:5433 and embed server on localhost:8088.
Run: pytest tests/integration/ -v
"""
from __future__ import annotations

from munin.core.config import MuninConfig
from munin.core.memory import forget, list_projects, recall, remember, show


def test_remember_and_recall_roundtrip(cfg: MuninConfig) -> None:
    """remember two thoughts, recall returns at least one with similarity > 0."""
    remember(
        "the database uses pgvector for similarity search",
        project="pytest_integration",
        config=cfg,
    )
    remember(
        "embeddings are computed with nomic-embed-text",
        project="pytest_integration",
        config=cfg,
    )

    results = recall(
        "vector similarity database", project="pytest_integration", config=cfg
    )

    assert len(results) >= 1
    assert all(r.similarity > 0.0 for r in results)
    assert all(r.project == "pytest_integration" for r in results)


def test_recall_project_filter(cfg: MuninConfig) -> None:
    """recall with project= filter excludes rows from other projects."""
    remember(
        "alpha project stores vector embeddings",
        project="pytest_integration_a",
        config=cfg,
    )
    remember(
        "beta project stores relational data",
        project="pytest_integration_b",
        config=cfg,
    )

    results = recall(
        "vector embeddings", project="pytest_integration_a", config=cfg
    )

    returned_projects = {r.project for r in results}
    assert "pytest_integration_b" not in returned_projects


def test_dedup_via_upsert(cfg: MuninConfig) -> None:
    """remembering identical content+project twice results in exactly one row."""
    content = "dedup test: munin uses content fingerprint for deduplication"
    remember(content, project="pytest_integration", config=cfg)
    remember(content, project="pytest_integration", config=cfg)

    project_counts = dict(list_projects(config=cfg))
    assert project_counts.get("pytest_integration", 0) == 1

    results = recall(content, project="pytest_integration", config=cfg)
    assert len(results) == 1


def test_show_and_forget(cfg: MuninConfig) -> None:
    """show returns thought; forget deletes it; subsequent show returns None; second forget returns False."""
    uid = remember(
        "show and forget test thought",
        project="pytest_integration",
        config=cfg,
    )

    thought = show(uid, config=cfg)
    assert thought is not None
    assert thought.id == uid
    assert thought.content == "show and forget test thought"

    assert forget(uid, config=cfg) is True
    assert show(uid, config=cfg) is None
    assert forget(uid, config=cfg) is False


def test_list_projects_ordering(cfg: MuninConfig) -> None:
    """list_projects returns all projects sorted alphabetically."""
    remember("zz thought", project="zz_proj_int", config=cfg)
    remember("aa thought", project="aa_proj_int", config=cfg)
    remember("mm thought", project="mm_proj_int", config=cfg)

    projects = list_projects(config=cfg)
    names = [p for p, _ in projects]

    assert "aa_proj_int" in names
    assert "mm_proj_int" in names
    assert "zz_proj_int" in names

    # Verify the three test projects appear in sorted order relative to each other
    test_names = [n for n in names if n in {"aa_proj_int", "mm_proj_int", "zz_proj_int"}]
    assert test_names == sorted(test_names)
