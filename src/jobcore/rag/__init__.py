"""RAG lane for the job-search corpus.

Ships four things the audit named as gaps, in one place:
freshness/invalidation (upsert + delete-by-source), reranking, a blocking
golden-question gate, and LLM-as-judge over the existing free/local
`jobcore.llm` adapter.

Heavy dependencies (ChromaDB, sentence-transformers) live behind the ``rag``
optional extra and behind Protocol seams, so the core install stays near-stdlib
and the logic stays testable without them.
"""

from __future__ import annotations

from .chunk import Chunk, chunk_markdown
from .evalset import GateResult, RagExtraMissing, evaluate, run_gate
from .goldens import DEFAULT_GOLDENS, EvalReport, Golden, GoldenOutcome, score_golden
from .rerank import RerankUnavailable, cross_encoder_scorer, rerank
from .store import Hit, IngestResult, InMemoryBackend, Store, VectorBackend, lexical_score

__all__ = [
    "DEFAULT_GOLDENS",
    "Chunk",
    "EvalReport",
    "GateResult",
    "Golden",
    "GoldenOutcome",
    "Hit",
    "InMemoryBackend",
    "IngestResult",
    "RagExtraMissing",
    "RerankUnavailable",
    "Store",
    "VectorBackend",
    "chunk_markdown",
    "cross_encoder_scorer",
    "evaluate",
    "lexical_score",
    "rerank",
    "run_gate",
    "score_golden",
]
