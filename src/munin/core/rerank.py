"""Cross-encoder reranking client for the llama-rerank sidecar (P2-4).

Sends (query, documents) to POST /reranking on the bge-reranker-v2-m3 sidecar
and returns the documents reordered by cross-encoder relevance_score (desc).

The sidecar is optional — callers must handle the case where it is unavailable
by catching MuninRerankUnavailable and falling back to the original order.
"""

from __future__ import annotations

import atexit
import logging

import httpx

logger = logging.getLogger(__name__)

# P2-fix(5): Module-level reusable client with structured timeout.
# This avoids opening a new connection per recall() call.
# The client is created lazily on first use.
# P3-fix(C8): read timeout raised 10s → 30s.  bge-reranker-v2-m3-Q8_0 on CPU
# scores 25 docs (after P3-fix top_n lowering) in ~8s; 30s gives comfortable
# headroom while still failing fast on a truly hung sidecar.
_client: httpx.Client | None = None
_TIMEOUT = httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=5.0)


def _close_client() -> None:
    """S2: atexit handler — close the module-level httpx.Client on process exit.

    The long-lived MCP server process holds this client open for the duration of
    its lifetime.  Without explicit close(), the underlying connection pool leaks
    file descriptors.  Registered once at module import time.
    """
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        _client = None


atexit.register(_close_client)


class MuninRerankUnavailable(Exception):
    """Raised when the reranker sidecar cannot be reached or returns bad data."""


def _get_client() -> httpx.Client:
    """Return the module-level httpx.Client, creating it on first call."""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=_TIMEOUT)
    return _client


def rerank(
    query: str,
    documents: list[str],
    *,
    rerank_url: str,
    client: httpx.Client | None = None,
) -> tuple[list[int], list[float]]:
    """Return document indices sorted by cross-encoder relevance score (desc).

    Sends a POST /reranking request to the sidecar.  The sidecar uses the
    bge-reranker-v2-m3 model which expects {"query": str, "documents": [str]}.
    The response shape is:
      {"results": [{"index": int, "relevance_score": float}, ...]}

    Args:
        query:      The search query string.
        documents:  List of document strings to score against the query.
        rerank_url: Base URL of the llama-rerank sidecar, e.g. http://localhost:8089.
        client:     Optional httpx.Client to use (for testing / caller-managed pools).
                    Defaults to the module-level reusable client.

    Returns:
        Tuple of (sorted_indices, relevance_scores) where sorted_indices are the
        original document indices sorted by relevance_score descending, and
        relevance_scores are the corresponding scores in that order.
        Length of both lists equals len(documents).

    Raises:
        MuninRerankUnavailable: If the sidecar is unreachable, times out, returns a
            non-200 status, returns empty results, or returns malformed JSON/data.
    """
    if not documents:
        return [], []

    url = f"{rerank_url.rstrip('/')}/reranking"
    payload = {"query": query, "documents": documents}
    _c = client or _get_client()

    try:
        resp = _c.post(url, json=payload)
    except httpx.TimeoutException as exc:
        # P2-fix(2c): catch all timeout variants (Read/Write/Pool/Connect timeouts).
        raise MuninRerankUnavailable(
            f"rerank sidecar timed out at {url}: {exc}"
        ) from exc
    except httpx.TransportError as exc:
        # P2-fix(2c): catch all transport errors (ConnectError, etc.).
        raise MuninRerankUnavailable(
            f"rerank sidecar unreachable at {url}: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise MuninRerankUnavailable(
            f"rerank sidecar returned HTTP {resp.status_code} at {url}"
        )

    # P2-fix(2b): wrap JSON parsing + result field access in try/except.
    try:
        data = resp.json()
        results = data.get("results", [])
    except (ValueError, TypeError) as exc:
        raise MuninRerankUnavailable(
            f"rerank sidecar returned non-JSON response at {url}: {exc}"
        ) from exc

    # P2-fix(2a): empty results list is a sidecar failure — do not silently
    # collapse to the tail slice; raise so caller falls back to hybrid order.
    if not results:
        raise MuninRerankUnavailable(
            f"rerank sidecar returned empty results list at {url}"
        )

    try:
        sorted_results = sorted(
            results, key=lambda r: r["relevance_score"], reverse=True
        )
        indices = [int(r["index"]) for r in sorted_results]
        scores = [float(r["relevance_score"]) for r in sorted_results]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # P2-fix(2b): missing keys or wrong types in result dicts.
        raise MuninRerankUnavailable(
            f"rerank sidecar returned malformed result entries at {url}: {exc}"
        ) from exc

    return indices, scores
