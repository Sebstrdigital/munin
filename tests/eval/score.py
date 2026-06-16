"""Recall-quality eval scorer for munin.

Loads eval_set.json, runs each query through the REAL core recall path
(munin.core.memory.recall), and computes recall@1, recall@5, recall@10
and MRR over the full set.

Usage:
    python tests/eval/score.py --out tests/eval/baseline_nomic.json
    python tests/eval/score.py --out tests/eval/post_final_gemma.json \
        --model embeddinggemma-300M-Q8_0

Notes:
    - Calls the production recall() function directly — the same code path
      used by the CLI and MCP server.  Post-reindex re-scoring is apples-to-
      apples because the function, SQL, and config are identical; only the
      stored vectors change.
    - Each recall() call bumps hit_count / last_hit_at on the returned
      thoughts in the prod DB (the UPDATE at the end of recall()).  This is
      acceptable: both the baseline run and the post-reindex re-score bump
      equally, so they do not affect the relative comparison.
    - Scoping: eval pairs with scope=null call recall() without a scope
      filter, matching production agent behaviour.
    - k=10 is used for recall(); results contain up to 10 thoughts per query.
    - Item 16: before scoring, each pair's expected_thought_id is checked to
      be live (superseded_by IS NULL AND valid_to IS NULL).  Retired expected
      thoughts are reported as confounders and excluded from metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# Ensure the src tree is importable when run as a standalone script
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from munin.core.config import load as load_config  # noqa: E402
from munin.core.db import get_pool as _get_pool  # noqa: E402
from munin.core.memory import recall  # noqa: E402

_EVAL_SET = Path(__file__).parent / "eval_set.json"
_RECALL_K = 10


def _rank_of(results_ids: list[str], expected: str) -> int | None:
    """Return 1-based rank of expected in results, or None if absent."""
    for i, rid in enumerate(results_ids, start=1):
        if rid == expected:
            return i
    return None


def _check_live_thoughts(
    expected_ids: list[str], cfg: Any
) -> tuple[set[str], list[str]]:
    """Return (live_ids, retired_ids).

    Queries the DB to verify which expected thought IDs are still live.
    A thought is live iff superseded_by IS NULL (migration 010) AND
    valid_to IS NULL (migration 011) — matching the default-recall filter
    so a bitemporally-retired expected thought is correctly treated as a
    confounder rather than silently counted.
    """
    if not expected_ids:
        return set(), []
    pool = _get_pool(cfg)
    pool.open(wait=True)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM thoughts"
                " WHERE id = ANY(%s::uuid[])"
                " AND superseded_by IS NULL"
                " AND valid_to IS NULL",
                (expected_ids,),
            )
            live = {str(row[0]) for row in cur.fetchall()}
    retired = [eid for eid in expected_ids if eid not in live]
    return live, retired


def main(out_path: str | None, model_name: str | None) -> None:
    eval_pairs: list[dict[str, Any]] = json.loads(_EVAL_SET.read_text())
    cfg = load_config()

    # Item 15: model label — use --model arg if provided, else embed_url as hint.
    _model_label = model_name or f"unknown (embed_url={cfg.embed_url})"

    # Item 16: verify expected thoughts are still live before scoring.
    all_expected = [p["expected_thought_id"] for p in eval_pairs]
    live_ids, retired_ids = _check_live_thoughts(all_expected, cfg)
    if retired_ids:
        sys.stderr.write(
            f"[WARN] {len(retired_ids)} expected thought(s) are RETIRED (superseded/expired)"
            " — these pairs are excluded from metrics as confounders:\n"
        )
        for rid in retired_ids:
            sys.stderr.write(f"  {rid}\n")

    hits_at_1 = 0
    hits_at_5 = 0
    hits_at_10 = 0
    reciprocal_ranks: list[float] = []

    per_query: list[dict[str, Any]] = []
    excluded = 0

    for pair in eval_pairs:
        pid = pair["id"]
        query = pair["query"]
        expected_id = pair["expected_thought_id"]
        project = pair["project"]
        scope = pair.get("scope")  # None means no scope filter

        # Item 16: skip retired expected thoughts; they confound the gate.
        if expected_id not in live_ids:
            excluded += 1
            per_query.append({
                "pair_id": pid,
                "project": project,
                "expected_id": expected_id,
                "rank": None,
                "hit@1": 0,
                "hit@5": 0,
                "hit@10": 0,
                "rr": 0.0,
                "excluded": "expected_thought_retired",
            })
            continue

        try:
            results = recall(
                query,
                project=project,
                scope=scope if scope else None,
                limit=_RECALL_K,
                config=cfg,
            )
        except Exception as exc:  # noqa: BLE001
            # Log failure per-query; count as miss for all metrics.
            sys.stderr.write(f"[WARN] pair {pid} recall() raised: {exc}\n")
            results = []

        result_ids = [str(r.id) for r in results]
        rank = _rank_of(result_ids, expected_id)

        hit1 = int(rank == 1) if rank else 0
        hit5 = int(rank is not None and rank <= 5)
        hit10 = int(rank is not None and rank <= 10)
        rr = (1.0 / rank) if rank else 0.0

        hits_at_1 += hit1
        hits_at_5 += hit5
        hits_at_10 += hit10
        reciprocal_ranks.append(rr)

        per_query.append({
            "pair_id": pid,
            "project": project,
            "expected_id": expected_id,
            "rank": rank,
            "hit@1": hit1,
            "hit@5": hit5,
            "hit@10": hit10,
            "rr": round(rr, 4),
        })

    n_scored = len(eval_pairs) - excluded
    n = len(eval_pairs)
    denom = n_scored if n_scored > 0 else 1

    summary: dict[str, Any] = {
        "n_total": n,
        "n_scored": n_scored,
        "n_excluded_retired": excluded,
        "recall_at_1": round(hits_at_1 / denom, 4),
        "recall_at_5": round(hits_at_5 / denom, 4),
        "recall_at_10": round(hits_at_10 / denom, 4),
        "mrr": round(sum(reciprocal_ranks) / denom, 4) if reciprocal_ranks else 0.0,
        "scored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": _model_label,
        "per_query": per_query,
    }

    print(json.dumps({k: v for k, v in summary.items() if k != "per_query"}, indent=2))

    if out_path:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2))
        sys.stderr.write(f"[INFO] wrote {out}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score recall quality eval set")
    parser.add_argument("--out", default=None, help="Path to write JSON summary")
    parser.add_argument(
        "--model",
        default=None,
        help="Embedding model name to label in output JSON (e.g. embeddinggemma-300M-Q8_0)",
    )
    args = parser.parse_args()
    main(args.out, args.model)
