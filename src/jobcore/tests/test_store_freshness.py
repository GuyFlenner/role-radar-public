"""The freshness invariant — the reason src/jobcore/rag exists.

Mirrors a real production defect found in `ai-chat` on 2026-08-01: ingestion is
add-only, so an edited source leaves orphaned chunks that stay retrievable and get
cited with false confidence.

`test_shortened_source_leaves_no_orphan_chunks` is the regression proof. It MUST
fail against an add-only implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.jobcore.rag.store import InMemoryBackend, Store


@pytest.fixture
def rag_store() -> Store:
    return Store(InMemoryBackend())


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestFreshness:
    def test_shortened_source_leaves_no_orphan_chunks(self, tmp_path: Path, rag_store: Store) -> None:
        """RED against add-only: chunks a.md:1 and a.md:2 survive and this fails."""
        doc = _write(tmp_path / "a.md", "# A\n\npara one\n\npara two\n\npara three\n")
        first = rag_store.ingest(doc, collection="corpus")
        assert first.chunks_written >= 3, "fixture should produce several chunks"

        _write(doc, "# A\n\npara one\n")  # the source shrinks
        second = rag_store.ingest(doc, collection="corpus")

        assert second.chunks_deleted > 0, "invalidation never ran — ingestion is add-only"
        remaining = rag_store.chunk_ids(source="a.md", collection="corpus")
        assert remaining == ["a.md:0", "a.md:1"], f"orphaned chunks survived: {remaining}"

    def test_reingesting_unchanged_source_is_stable(self, tmp_path: Path, rag_store: Store) -> None:
        """Determinism guard: same content twice must not duplicate or delete."""
        doc = _write(tmp_path / "b.md", "# B\n\nonly para\n")
        rag_store.ingest(doc, collection="corpus")
        before = rag_store.chunk_ids(source="b.md", collection="corpus")

        second = rag_store.ingest(doc, collection="corpus")

        assert rag_store.chunk_ids(source="b.md", collection="corpus") == before
        assert second.chunks_deleted == 0, "nothing changed, so nothing should be deleted"

    def test_delete_by_source_removes_everything_and_counts(self, tmp_path: Path, rag_store: Store) -> None:
        doc = _write(tmp_path / "c.md", "# C\n\none\n\ntwo\n")
        written = rag_store.ingest(doc, collection="corpus").chunks_written

        deleted = rag_store.delete_by_source(source="c.md", collection="corpus")

        assert deleted == written
        assert rag_store.chunk_ids(source="c.md", collection="corpus") == []

    def test_other_sources_are_untouched_by_invalidation(self, tmp_path: Path, rag_store: Store) -> None:
        """Invalidation must be scoped to the source being ingested."""
        keep = _write(tmp_path / "keep.md", "# Keep\n\nalpha\n\nbeta\n")
        rag_store.ingest(keep, collection="corpus")
        target = _write(tmp_path / "target.md", "# T\n\none\n\ntwo\n\nthree\n")
        rag_store.ingest(target, collection="corpus")

        _write(target, "# T\n\none\n")
        rag_store.ingest(target, collection="corpus")

        assert rag_store.chunk_ids(source="keep.md", collection="corpus") == [
            "keep.md:0",
            "keep.md:1",
            "keep.md:2",
        ]
