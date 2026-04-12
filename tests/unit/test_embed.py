"""Unit tests for core.embed using httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from munin.core.config import MuninConfig
from munin.core.embed import embed, embed_batch
from munin.core.errors import MuninEmbedError

_DIM = 768


def _make_cfg(batch_size: int = 32) -> MuninConfig:
    return MuninConfig(
        db_url="postgresql://munin:munin@localhost:5433/munin",
        embed_url="http://embed-server",
        embed_dim=_DIM,
        default_limit=10,
        embed_batch_size=batch_size,
    )


def _embedding_response(
    embeddings: list[list[float]],
    status_code: int = 200,
) -> httpx.Response:
    """Build a fake llama.cpp /v1/embeddings response."""
    data = [
        {"index": i, "embedding": vec, "object": "embedding"}
        for i, vec in enumerate(embeddings)
    ]
    body = json.dumps({"object": "list", "data": data, "model": "nomic"})
    return httpx.Response(status_code, text=body)


def _single_vec() -> list[float]:
    return [0.1] * _DIM


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------


def test_embed_returns_768_floats(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed() returns exactly embed_dim floats."""
    vec = _single_vec()

    def handler(request: httpx.Request) -> httpx.Response:
        return _embedding_response([vec])

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    result = embed("hello", config=_make_cfg())
    assert len(result) == _DIM
    assert all(isinstance(v, float) for v in result)


def test_embed_values_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed() preserves the embedding values returned by the server."""
    vec = [float(i) / _DIM for i in range(_DIM)]

    def handler(request: httpx.Request) -> httpx.Response:
        return _embedding_response([vec])

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    result = embed("test", config=_make_cfg())
    assert result == pytest.approx(vec)


def test_embed_raises_on_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed() raises MuninEmbedError when the server is unreachable."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    with pytest.raises(MuninEmbedError, match="embed server unreachable"):
        embed("hello", config=_make_cfg())


def test_embed_raises_on_5xx_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed() raises MuninEmbedError after exhausting retries on 5xx."""
    # Patch sleep so tests run instantly
    monkeypatch.setattr("munin.core.embed.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text='{"error": "overloaded"}')

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    with pytest.raises(MuninEmbedError, match="embed server unreachable"):
        embed("hello", config=_make_cfg())


# ---------------------------------------------------------------------------
# embed_batch()
# ---------------------------------------------------------------------------


def test_embed_batch_returns_correct_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed_batch() returns one vector per input text."""
    texts = ["a", "b", "c"]
    vecs = [_single_vec() for _ in texts]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        n = len(body["input"]) if isinstance(body["input"], list) else 1
        return _embedding_response(vecs[:n])

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    result = embed_batch(texts, config=_make_cfg())
    assert len(result) == 3
    assert all(len(v) == _DIM for v in result)


def test_embed_batch_respects_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed_batch() splits input into chunks of batch_size."""
    texts = [f"text-{i}" for i in range(5)]
    call_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        chunk = body["input"]
        n = len(chunk) if isinstance(chunk, list) else 1
        call_sizes.append(n)
        return _embedding_response([_single_vec() for _ in range(n)])

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    # batch_size=2 → calls of size 2, 2, 1
    result = embed_batch(texts, config=_make_cfg(batch_size=2))
    assert call_sizes == [2, 2, 1]
    assert len(result) == 5


def test_embed_batch_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed_batch() returns vectors in the same order as the input texts."""
    texts = ["first", "second", "third"]
    # Distinct vectors so we can verify ordering
    vecs = [[float(i)] * _DIM for i in range(len(texts))]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        chunk: list[str] = body["input"] if isinstance(body["input"], list) else [body["input"]]
        n = len(chunk)
        # Return in reverse index order to exercise sorting
        data = [
            {"index": n - 1 - j, "embedding": vecs[n - 1 - j], "object": "embedding"}
            for j in range(n)
        ]
        body_out = json.dumps({"object": "list", "data": data, "model": "nomic"})
        return httpx.Response(200, text=body_out)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    result = embed_batch(texts, config=_make_cfg(batch_size=3))
    for i, vec in enumerate(result):
        assert vec == pytest.approx(vecs[i])


def test_embed_batch_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed_batch([]) returns an empty list without making any HTTP calls."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="{}")

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    result = embed_batch([], config=_make_cfg())
    assert result == []
    assert not called


def test_embed_batch_raises_on_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed_batch() raises MuninEmbedError when the server is unreachable."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))

    with pytest.raises(MuninEmbedError, match="embed server unreachable"):
        embed_batch(["hello"], config=_make_cfg())
