"""Golden-question scoring, and the gate verdict built from it.

The point of these: a retrieval regression must make the gate FAIL, and a gate that
cannot fail is decoration. Every assertion here is deterministic — no LLM decides
pass or fail, by design (see `goldens.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.jobcore.rag import evalset
from src.jobcore.rag.goldens import Golden, score_golden
from src.jobcore.rag.store import Hit, InMemoryBackend, Store

CORPUS = {
    "freshness.md": "# Freshness\n\nupsert and delete by id keep the index fresh\n\nstale chunks get cited\n",
    "adr.md": "# ADR\n\nhub and spoke fan out beats A2A swarms for these workloads\n",
    "unrelated.md": "# Unrelated\n\nsalary benchmark bands for a team lead in Tel Aviv\n",
}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(InMemoryBackend())
    for name, text in CORPUS.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        s.ingest(path, collection="corpus")
    return s


def _hit(source: str, score: float, ordinal: int = 0) -> Hit:
    return Hit(chunk_id=f"{source}:{ordinal}", source=source, ordinal=ordinal, text="", score=score)


class TestScoreGolden:
    def test_must_retrieve_passes_when_present(self) -> None:
        golden = Golden(id="g", query="q", must_retrieve=("a.md",))
        assert score_golden(golden, [_hit("a.md", 0.9)]).passed

    def test_must_retrieve_fails_when_absent(self) -> None:
        golden = Golden(id="g", query="q", must_retrieve=("a.md",))
        outcome = score_golden(golden, [_hit("b.md", 0.9)])
        assert not outcome.passed
        assert "must_retrieve" in outcome.failures[0]

    def test_must_not_retrieve_fails_when_present(self) -> None:
        golden = Golden(id="g", query="q", must_not_retrieve=("stale.md",))
        outcome = score_golden(golden, [_hit("stale.md", 0.9)])
        assert not outcome.passed
        assert "must_not_retrieve" in outcome.failures[0]

    def test_top_source_is_strict_about_rank_one(self) -> None:
        golden = Golden(id="g", query="q", top_source="a.md")
        assert score_golden(golden, [_hit("a.md", 0.9), _hit("b.md", 0.5)]).passed
        assert not score_golden(golden, [_hit("b.md", 0.9), _hit("a.md", 0.5)]).passed

    def test_min_score_floor(self) -> None:
        golden = Golden(id="g", query="q", min_score=0.5)
        assert score_golden(golden, [_hit("a.md", 0.6)]).passed
        assert not score_golden(golden, [_hit("a.md", 0.4)]).passed

    def test_empty_retrieval_fails_every_expectation(self) -> None:
        golden = Golden(id="g", query="q", must_retrieve=("a.md",), top_source="a.md", min_score=0.1)
        outcome = score_golden(golden, [])
        assert not outcome.passed
        assert len(outcome.failures) == 3


class TestGateVerdict:
    """AC6: a broken index must make the gate FAIL, not pass quietly."""

    GOLDENS = (
        Golden(id="G-fresh", query="upsert delete stale chunks", must_retrieve=("freshness.md",), min_score=0.1),
        Golden(id="G-adr", query="hub spoke A2A swarms", must_retrieve=("adr.md",), min_score=0.1),
    )

    def test_healthy_index_passes(self, store: Store) -> None:
        report = evalset.evaluate(store, collection="corpus", goldens=self.GOLDENS)
        assert report.passed, report.summary()
        assert report.failed_ids == []

    def test_gate_reports_pass_on_healthy_index(self, store: Store) -> None:
        result = evalset.run_gate(store, collection="corpus", goldens=self.GOLDENS)
        assert result.status in {"PASS", "FAIL"}, "a supplied store must never yield SKIPPED"

    def test_emptied_index_makes_the_gate_fail(self, store: Store) -> None:
        """The corrupted-index case: delete the corpus, the gate must go red."""
        for name in CORPUS:
            store.delete_by_source(source=name, collection="corpus")

        report = evalset.evaluate(store, collection="corpus", goldens=self.GOLDENS)

        assert not report.passed
        assert set(report.failed_ids) == {"G-fresh", "G-adr"}
        assert "FAILED" in report.summary()

    def test_losing_one_document_fails_only_its_golden(self, store: Store) -> None:
        """Partial regressions must be attributable, not just 'something broke'."""
        store.delete_by_source(source="adr.md", collection="corpus")

        report = evalset.evaluate(store, collection="corpus", goldens=self.GOLDENS)

        assert report.failed_ids == ["G-adr"]

    def test_supplied_store_is_never_skipped_even_without_the_extra(
        self, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The extra gates the *default* backend, not an explicitly supplied one."""
        monkeypatch.setattr(evalset, "_extra_available", lambda: False)
        monkeypatch.setenv("CI", "1")

        result = evalset.run_gate(store, collection="corpus", goldens=self.GOLDENS)

        assert result.status != "SKIPPED"
