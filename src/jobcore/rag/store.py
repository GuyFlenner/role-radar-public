"""The RAG store, and the freshness contract that is the whole point of it.

`ai-chat`'s production ingestion is **add-only**: re-ingesting an edited source
writes the new chunks but never removes the ones the source no longer produces.
Those orphans stay retrievable and get **cited with false confidence** — a
correctness defect, not a tidiness one. See
`architecture-workflows/AI_ECOSYSTEM_GAP_AUDIT_2026-08-01.md`.

This module exists to get that right, so the behaviour is expressed as an
invariant with a test that fails against an add-only implementation:

    after ingest(source), the stored ids for that source are EXACTLY the ids the
    current content produces — no more, no fewer.

The backend is a Protocol so the logic is testable with zero heavy dependencies;
ChromaDB is one adapter behind it, not a hard requirement of the core.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .chunk import Chunk, chunk_markdown


@dataclass(frozen=True)
class Hit:
    """A retrieval result. ``rerank_score`` stays None until a reranker runs."""

    chunk_id: str
    source: str
    ordinal: int
    text: str
    score: float
    rerank_score: float | None = None


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingest.

    ``chunks_deleted`` is the observable proof that invalidation ran; an add-only
    implementation can only ever report 0.
    """

    source: str
    chunks_written: int
    chunks_deleted: int
    content_hash: str


def lexical_score(query: str, text: str) -> float:
    """Deterministic token-overlap score in [0, 1].

    Not a stand-in for embeddings — it cannot match paraphrases that share no
    tokens, which is exactly why semantic tool discovery needs the real backend.
    Its job is to make the retrieval plumbing and the golden-question properties
    testable with zero dependencies and zero nondeterminism.
    """
    q = _tokens(query)
    d = _tokens(text)
    if not q or not d:
        return 0.0
    overlap = len(q & d)
    return overlap / len(q) if overlap else 0.0


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}


class VectorBackend(Protocol):
    """The storage seam. ChromaDB is an adapter; tests use InMemoryBackend."""

    def upsert(self, collection: str, chunks: list[Chunk]) -> None: ...

    def delete_ids(self, collection: str, ids: list[str]) -> int: ...

    def ids_for_source(self, collection: str, source: str) -> list[str]: ...

    def query(self, collection: str, text: str, k: int) -> list[Hit]: ...


class InMemoryBackend:
    """Stdlib-only backend. Keeps the freshness logic testable without ChromaDB."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, Chunk]] = {}

    def upsert(self, collection: str, chunks: list[Chunk]) -> None:
        store = self._collections.setdefault(collection, {})
        for chunk in chunks:
            store[chunk.chunk_id] = chunk

    def delete_ids(self, collection: str, ids: list[str]) -> int:
        store = self._collections.setdefault(collection, {})
        deleted = 0
        for chunk_id in ids:
            if store.pop(chunk_id, None) is not None:
                deleted += 1
        return deleted

    def ids_for_source(self, collection: str, source: str) -> list[str]:
        store = self._collections.get(collection, {})
        return sorted(cid for cid, chunk in store.items() if chunk.source == source)

    def query(self, collection: str, text: str, k: int) -> list[Hit]:
        store = self._collections.get(collection, {})
        scored = [
            Hit(
                chunk_id=chunk.chunk_id,
                source=chunk.source,
                ordinal=chunk.ordinal,
                text=chunk.text,
                score=lexical_score(text, chunk.text),
            )
            for chunk in store.values()
        ]
        # chunk_id as the tiebreak keeps ordering total and reproducible.
        ranked = sorted((hit for hit in scored if hit.score > 0), key=lambda h: (-h.score, h.chunk_id))
        return ranked[:k]


class Store:
    """Ingest and inspect chunks, upholding the freshness invariant above."""

    def __init__(self, backend: VectorBackend) -> None:
        self._backend = backend

    def ingest(self, path: Path, *, collection: str, source: str | None = None) -> IngestResult:
        """Write the source's current chunks and delete the ones it no longer produces.

        The deletion step is what separates this from `ai-chat`'s add-only
        ingestion. Orphans are computed as ``stored_ids - current_ids`` and scoped
        to this source, so re-ingesting one document never disturbs another.

        ``source`` overrides the default of ``path.name``. Needed whenever the
        filename is not the identity: every skill file is called ``SKILL.md``, so
        they must be keyed on the skill name or they all collide into one source.
        """
        return self.ingest_text(
            path.read_text(encoding="utf-8"),
            source=source or path.name,
            collection=collection,
        )

    def ingest_text(self, text: str, *, source: str, collection: str) -> IngestResult:
        """Same freshness contract as :meth:`ingest`, for content not on disk."""
        chunks = chunk_markdown(text, source=source)

        current_ids = {chunk.chunk_id for chunk in chunks}
        stored_ids = set(self._backend.ids_for_source(collection, source))
        orphans = sorted(stored_ids - current_ids)

        deleted = self._backend.delete_ids(collection, orphans) if orphans else 0
        self._backend.upsert(collection, chunks)

        return IngestResult(
            source=source,
            chunks_written=len(chunks),
            chunks_deleted=deleted,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def chunk_ids(self, *, source: str, collection: str) -> list[str]:
        return self._backend.ids_for_source(collection, source)

    def delete_by_source(self, *, source: str, collection: str) -> int:
        ids = self._backend.ids_for_source(collection, source)
        return self._backend.delete_ids(collection, ids)

    def query(self, text: str, *, collection: str, k: int = 50) -> list[Hit]:
        """Retrieve up to ``k`` hits, best first. Ranking is delegated to the backend."""
        return self._backend.query(collection, text, k)
