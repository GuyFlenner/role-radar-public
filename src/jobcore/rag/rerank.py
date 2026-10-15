"""Second-stage reranking.

A bi-encoder scores query and document *independently*, so it is systematically
bad at one thing: a passage that shares surface tokens with the query but is about
something else can outrank the passage that actually answers it. A cross-encoder
scores the (query, passage) **pair** jointly and fixes exactly that class of error.

`ai-chat` has no rerank stage at all — `retrieval.py::curate_rag_results` only
threshold-filters the first-stage score (audit, 2026-08-01). This module is the
version that does.

The scorer is injected, which is what makes the ordering logic testable without
downloading a model: tests pass a fake scorer with known preferences, production
passes `CrossEncoderScorer`. That also keeps `sentence-transformers` (and torch) in
the separate ``rerank`` extra rather than in CI's install.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from .store import Hit

Scorer = Callable[[str, str], float]
"""(query, passage) -> relevance. Higher is better. Any scale; only order matters."""


class RerankUnavailable(RuntimeError):
    """The ``rerank`` extra is not installed, so no cross-encoder can be built."""


def rerank(query: str, hits: list[Hit], *, scorer: Scorer, top_k: int = 8) -> list[Hit]:
    """Re-score ``hits`` with ``scorer`` and return the best ``top_k``, best first.

    Every returned Hit carries its ``rerank_score``, so a caller can always show
    both stages and explain *why* an order changed. Ties break on ``chunk_id`` to
    keep the result total and reproducible.
    """
    if not hits:
        return []

    rescored = [replace(hit, rerank_score=scorer(query, hit.text)) for hit in hits]
    rescored.sort(key=lambda h: (-(h.rerank_score or 0.0), h.chunk_id))
    return rescored[:top_k]


def cross_encoder_scorer(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> Scorer:
    """Build a real cross-encoder scorer.

    Raises:
        RerankUnavailable: when the ``rerank`` extra is not installed. Loud by
            design — silently degrading to first-stage order would hide the fact
            that reranking never happened.
    """
    try:
        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RerankUnavailable("cross-encoder reranking needs the 'rerank' extra (`uv sync --extra rerank`)") from exc

    model = CrossEncoder(model_name)

    def score(query: str, passage: str) -> float:
        return float(model.predict([(query, passage)])[0])

    return score
