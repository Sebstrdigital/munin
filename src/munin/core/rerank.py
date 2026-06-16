"""Cross-encoder reranking client for the llama-rerank sidecar (P2-4).

Sends (query, documents) to POST /reranking on the bge-reranker-v2-m3 sidecar
and returns the documents reordered by cross-encoder relevance_score (desc).

The sidecar is optional — callers must handle the case where it is unavailable
by catching MuninRerankUnavailable and falling back to the original order.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class MuninRerankUnavailable(Exception):
    """Raised when the reranker sidecar cannot be reached."""


def rerank(
    query: str,
    documents: list[str],
    *,
    rerank_url: str,
) -> list[int]:
    """Return document indices sorted by cross-encoder relevance score (desc).

    Sends a POST /reranking request to the sidecar.  The sidecar uses the
    bge-reranker-v2-m3 model which expects {"query": str, "documents": [str]}.
    The response shape is:
      {"results": [{"index": int, "relevance_score": float}, ...]}

    Args:
        query:      The search query string.
        documents:  List of document strings to score against the query.
        rerank_url: Base URL of the llama-rerank sidecar, e.g. http://localhost:8089.

    Returns:
        List of original document indices sorted by relevance_score descending.
        Length equals len(documents).

    Raises:
        MuninRerankUnavailable: If the sidecar is unreachable or returns an error.
    """
    if not documents:
        return []

    url = f"{rerank_url.rstrip('/')}/reranking"
    payload = {"query": query, "documents": documents}

    try:
        with httpx.Client() as client:
            resp = client.post(url, json=payload, timeout=_TIMEOUT)
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.TransportError) as exc:
        raise MuninRerankUnavailable(
            f"rerank sidecar unreachable at {url}: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise MuninRerankUnavailable(
            f"rerank sidecar returned HTTP {resp.status_code} at {url}"
        )

    data = resp.json()
    results = data.get("results", [])

    # Sort by relevance_score descending, then return the original indices.
    sorted_results = sorted(results, key=lambda r: r["relevance_score"], reverse=True)
    return [int(r["index"]) for r in sorted_results]
