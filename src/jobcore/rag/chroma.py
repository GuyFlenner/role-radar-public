"""ChromaDB adapter for `VectorBackend`.

Why this is a separate file behind a Protocol rather than the core: the store's
freshness logic must stay testable with zero dependencies (see `store.py`), and the
blocking gate must keep running on a bare clone. Chroma is therefore an *option*,
not a requirement.

What it buys that the lexical backend cannot: **paraphrase matching**. Chroma 1.5.9
ships an ONNX MiniLM default embedder — 384 dimensions, **no torch and no
sentence-transformers needed** — so real semantic retrieval comes from the light
`rag` extra alone. Verified: the query *"making my resume match a posting"* retrieves
*"tailoring a CV to a job description"*, which share no tokens at all and which the
lexical ranker scores 0.0. That capability is what AC8 (semantic tool discovery)
requires.

Distance → score: collections are created with cosine space, so similarity is
``1 - distance``, clamped at 0 because cosine distance can exceed 1 for opposed
vectors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .chunk import Chunk
from .store import Hit


class ChromaUnavailable(RuntimeError):
    """The ``rag`` extra is not installed, so no Chroma client can be built."""


def _import_chromadb() -> Any:
    try:
        import chromadb  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ChromaUnavailable("the Chroma backend needs the 'rag' extra (`uv sync --extra rag`)") from exc
    return chromadb


class ChromaBackend:
    """Embedding-backed store. Ephemeral by default so tests touch no disk.

    ⚠️ Never point ``path`` at an employer index (e.g. a work knowledge-base
    Chroma). This lane indexes *this* repository's own corpus only; mixing employer
    data into a personal repo is a privacy and IP problem, not a convenience.
    """

    def __init__(self, path: Path | None = None) -> None:
        chromadb = _import_chromadb()
        self._client = chromadb.PersistentClient(path=str(path)) if path is not None else chromadb.EphemeralClient()
        self._cache: dict[str, Any] = {}

    def _collection(self, collection: str) -> Any:
        if collection not in self._cache:
            # Collection names must be 3-512 chars of [a-zA-Z0-9._-] (chroma 1.5.9).
            self._cache[collection] = self._client.get_or_create_collection(
                name=collection,
                metadata={"hnsw:space": "cosine"},
            )
        return self._cache[collection]

    def upsert(self, collection: str, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        self._collection(collection).upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[{"source": c.source, "ordinal": c.ordinal} for c in chunks],
        )

    def delete_ids(self, collection: str, ids: list[str]) -> int:
        if not ids:
            return 0
        col = self._collection(collection)
        # Chroma's delete returns nothing, so count what was actually present.
        present = set(col.get(ids=ids).get("ids", []))
        col.delete(ids=ids)
        return len(present)

    def ids_for_source(self, collection: str, source: str) -> list[str]:
        got = self._collection(collection).get(where={"source": source})
        return sorted(got.get("ids", []))

    def query(self, collection: str, text: str, k: int) -> list[Hit]:
        col = self._collection(collection)
        if col.count() == 0:
            return []

        res = col.query(query_texts=[text], n_results=min(k, col.count()))
        ids = res["ids"][0]
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        dists = res["distances"][0]

        return [
            Hit(
                chunk_id=chunk_id,
                source=str(meta.get("source", "")),
                ordinal=int(meta.get("ordinal", 0)),
                text=doc,
                score=max(0.0, 1.0 - float(dist)),
            )
            for chunk_id, doc, meta, dist in zip(ids, docs, metas, dists, strict=True)
        ]
