"""Embedding client for the llama.cpp embedding server."""

from __future__ import annotations

import logging
import time
from typing import cast

import httpx

from munin.core.config import MuninConfig, load
from munin.core.errors import MuninEmbedError

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_RETRY_WAITS = (0.5, 1.0)


def _post_embeddings(
    client: httpx.Client,
    url: str,
    payload: dict[str, object],
) -> httpx.Response:
    """POST to the embeddings endpoint, retrying on 5xx up to 2 times."""
    last_resp: httpx.Response | None = None

    for attempt in range(len(_RETRY_WAITS) + 1):
        if attempt > 0:
            time.sleep(_RETRY_WAITS[attempt - 1])
        try:
            resp = client.post(url, json=payload, timeout=_TIMEOUT)
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            raise MuninEmbedError(
                f"embed server unreachable at {url}: {exc}"
            ) from exc

        if resp.status_code < 500:
            return resp

        last_resp = resp

    assert last_resp is not None
    raise MuninEmbedError(
        f"embed server unreachable at {url}: HTTP {last_resp.status_code}"
    )


def embed(
    text: str,
    *,
    config: MuninConfig | None = None,
    client: httpx.Client | None = None,
) -> list[float]:
    """Return the embedding vector for a single text string.

    Args:
        text: The input text to embed.
        config: Optional config override; uses load() if not provided.
        client: Optional shared httpx.Client to reuse across calls. When
            provided, the caller owns the client lifecycle and no new
            connection is opened. When None (default), a fresh client is
            created and closed after the call.

    Returns:
        A list of floats of length config.embed_dim.

    Raises:
        MuninEmbedError: If the embed server is unreachable or returns an error.
    """
    cfg = config if config is not None else load()
    url = f"{cfg.embed_url}/v1/embeddings"

    logger.debug("embed: url=%s text_len=%d", url, len(text))
    if client is not None:
        resp = _post_embeddings(client, url, {"input": text})
    else:
        with httpx.Client() as _client:
            resp = _post_embeddings(_client, url, {"input": text})

    if resp.status_code != 200:
        raise MuninEmbedError(
            f"embed server unreachable at {url}: HTTP {resp.status_code}"
        )

    data: list[dict[str, object]] = resp.json()["data"]
    return [float(v) for v in cast(list[float], data[0]["embedding"])]


def embed_batch(
    texts: list[str],
    *,
    config: MuninConfig | None = None,
) -> list[list[float]]:
    """Return embedding vectors for a list of texts, preserving input order.

    Internally splits into chunks of config.embed_batch_size and reassembles
    in the original order.

    Args:
        texts: Input texts to embed.
        config: Optional config override; uses load() if not provided.

    Returns:
        A list of embedding vectors, one per input text, in input order.

    Raises:
        MuninEmbedError: If the embed server is unreachable or returns an error.
    """
    if not texts:
        return []

    cfg = config if config is not None else load()
    url = f"{cfg.embed_url}/v1/embeddings"
    batch_size = cfg.embed_batch_size
    results: list[list[float]] = []

    with httpx.Client() as client:
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            resp = _post_embeddings(client, url, {"input": chunk})

            if resp.status_code != 200:
                raise MuninEmbedError(
                    f"embed server unreachable at {url}: HTTP {resp.status_code}"
                )

            data: list[dict[str, object]] = resp.json()["data"]
            # llama.cpp returns results sorted by index; preserve that ordering
            ordered = sorted(data, key=lambda d: cast(int, d["index"]))
            for item in ordered:
                results.append([float(v) for v in cast(list[float], item["embedding"])])

    return results
