"""Semantic tool discovery over the skill catalogue — AC8, measured honestly.

Requires the ``rag`` extra: Chroma's bundled ONNX MiniLM embedder (384-dim, no torch)
is what makes paraphrase matching possible at all. The lexical backend scores 0.0 on
a query that shares no tokens with the document, by construction.

**Measured on 2026-08-01 over 13 skills and 10 intent phrasings:**

    embedding-only  top-1 = 5/10,  top-3 = 10/10
    + cross-encoder top-1 = 8/10             ← AC8's target, reached

The two-stage result is the point. Embedding-only *misses* AC8 at 5/10, but recall is
**perfect** — the right skill is in the top 3 every single time — so the weakness was
never retrieval, it was ranking inside the candidate set. That is exactly the failure a
cross-encoder fixes, it was predicted from the recall/precision split *before* being
measured, and reranking then closed it: 5 → 8. Not monotonically (4 fixed, 1 broken),
which is worth knowing too.

These tests lock in what is true at each stage and ratchet each number, so an
improvement can be demonstrated and a regression cannot pass quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("chromadb", reason="semantic retrieval needs the 'rag' extra")

from src.jobcore.rag.cli import build_skills_store, collect_skills, skill_card  # noqa: E402
from src.jobcore.rag.store import InMemoryBackend, Store  # noqa: E402

# Each intent is phrased the way someone would actually ask, deliberately avoiding the
# skill's own name and, where possible, its vocabulary.
INTENTS: tuple[tuple[str, str], ...] = (
    ("I need to make my CV match a specific job posting", "cv-aligner"),
    ("figure out what this company does before I talk to them", "company-researcher"),
    ("draw me a picture of the system", "diagram-generator"),
    ("save where we got to so I can stop for today", "checkpoint"),
    ("am I about to overstate something on my resume", "job-flow-reviewer"),
    ("what questions might they throw at me", "interview-prep"),
    ("are there any new roles worth looking at", "job-radar"),
    ("narrow down a big spreadsheet of openings", "position-filter"),
    ("design the shape of a system before building it", "architect"),
    ("what is on my plate today", "good-morning"),
)

TOP1_BASELINE = 5  # measured 2026-08-01; may only go up
TOP3_BASELINE = 10


@pytest.fixture(scope="module")
def skills_store() -> Store:
    return build_skills_store(Path("."))


class TestSkillCards:
    def test_catalogue_is_discovered(self) -> None:
        skills = collect_skills(Path("."))
        assert len(skills) >= 10, f"expected the skill catalogue, found {len(skills)}"
        names = [name for _, name in skills]
        assert len(names) == len(set(names)), "skill names must be unique — they are the retrieval key"
        assert not any(n.startswith("_") for n in names), "_legacy must not be indexed"

    def test_card_is_the_advertisement_not_the_implementation(self) -> None:
        """A card must be short and description-shaped, or ranking degrades."""
        path = next(p for p, name in collect_skills(Path(".")) if name == "cv-aligner")
        card = skill_card(path)
        assert 0 < len(card) < 2000, f"card is {len(card)} chars — too long to be discriminative"
        assert "cv" in card.lower() or "resume" in card.lower()


class TestSemanticDiscovery:
    """The capability the lexical backend cannot provide."""

    # A genuinely token-disjoint pair. An earlier version of this test used
    # "making my resume match a posting", which shares the stopword "a" with the
    # document and so scored 0.167, not 0 — the test failed and was right to. Stopwords
    # are exactly how a "no shared tokens" claim quietly becomes false.
    PARAPHRASE = "resume matched against posting"
    RELEVANT = "tailoring CV to job description"
    DISTRACTOR = "kafka consumer topology with dead letter queues"

    def test_lexical_cannot_answer_a_token_disjoint_paraphrase(self) -> None:
        """Establishes the premise: embeddings are doing real work here, not luck."""
        lexical = Store(InMemoryBackend())
        lexical.ingest_text(self.RELEVANT, source="relevant", collection="c")
        lexical.ingest_text(self.DISTRACTOR, source="distractor", collection="c")

        assert lexical.query(self.PARAPHRASE, collection="c") == [], (
            "the query shares no token with either document, so lexical must return nothing"
        )

    def test_embeddings_rank_the_paraphrase_correctly(self) -> None:
        """The same query the lexical backend cannot answer at all."""
        from src.jobcore.rag.chroma import ChromaBackend

        semantic = Store(ChromaBackend())
        semantic.ingest_text(self.RELEVANT, source="relevant", collection="paraphrase-probe")
        semantic.ingest_text(self.DISTRACTOR, source="distractor", collection="paraphrase-probe")

        hits = semantic.query(self.PARAPHRASE, collection="paraphrase-probe", k=2)

        assert hits, "embeddings must return something where lexical returned nothing"
        assert hits[0].source == "relevant", f"ranked the distractor first: {[h.source for h in hits]}"

    def test_top3_recall_is_perfect_and_must_stay_perfect(self, skills_store: Store) -> None:
        """The strong, true guarantee: the right skill is always in the top 3."""
        misses = []
        for intent, expected in INTENTS:
            names = [h.source for h in skills_store.query(intent, collection="skills", k=3)]
            if expected not in names:
                misses.append((intent, expected, names))
        assert not misses, f"top-3 recall regressed: {misses}"

    def test_top1_precision_does_not_regress_below_the_measured_baseline(self, skills_store: Store) -> None:
        """The ratchet. AC8 wants >=8; reality is 5. Record it, don't dress it up."""
        hits = sum(
            1
            for intent, expected in INTENTS
            if [h.source for h in skills_store.query(intent, collection="skills", k=1)] == [expected]
        )
        assert hits >= TOP1_BASELINE, f"top-1 precision regressed: {hits}/{len(INTENTS)} < {TOP1_BASELINE}"

    @pytest.mark.xfail(
        reason="AC8 target is >=8/10 top-1; measured 5/10. Recall is 10/10, so the gap is "
        "ranking — the cross-encoder reranker is the identified fix. xfail(strict) so this "
        "flips to a failure the moment the target is actually reached.",
        strict=True,
    )
    def test_ac8_target_eight_of_ten_top1(self, skills_store: Store) -> None:
        hits = sum(
            1
            for intent, expected in INTENTS
            if [h.source for h in skills_store.query(intent, collection="skills", k=1)] == [expected]
        )
        assert hits >= 8


class TestRerankedPipeline:
    """The reranker measured on real data, not a planted decoy — AC8's target.

    ⚠️ **Verified locally, NOT gated in CI.** These tests need the `rerank` extra
    (sentence-transformers → torch, a multi-GB tree). CI installs only `rag`, so this
    class skips there. That is a deliberate trade, stated rather than hidden: CI gates
    the reranker's *ordering contract* deterministically with a fake scorer
    (`test_rag_rerank.py`), while the real-model numbers below are reproducible on
    demand with one command. Do not read a green CI run as evidence for these.

    Measured 2026-08-01 over 13 skills × 10 intents:
        embedding-only top-1 : 5/10
        + cross-encoder      : 8/10   (4 fixed, 1 broken, 5 unchanged)

    Reranking is not monotonic — it fixed four and broke one
    (`job-flow-reviewer` → `checkpoint`). Net +3.
    """

    RERANK_BASELINE = 8

    @staticmethod
    def _top1_with_rerank(store: Store, scorer: object) -> int:
        from src.jobcore.rag.rerank import rerank

        hits = 0
        for intent, expected in INTENTS:
            candidates = store.query(intent, collection="skills", k=5)
            reranked = rerank(intent, candidates, scorer=scorer, top_k=5)  # type: ignore[arg-type]
            if reranked and reranked[0].source == expected:
                hits += 1
        return hits

    def test_reranking_meets_the_ac8_target(self, skills_store: Store) -> None:
        """AC8: >=8/10 top-1. Embedding-only gives 5; the cross-encoder gets there."""
        pytest.importorskip("sentence_transformers", reason="needs the 'rerank' extra (torch)")
        from src.jobcore.rag.rerank import cross_encoder_scorer

        hits = self._top1_with_rerank(skills_store, cross_encoder_scorer())

        assert hits >= self.RERANK_BASELINE, f"reranked top-1 regressed: {hits}/{len(INTENTS)}"

    def test_reranking_beats_embedding_alone(self, skills_store: Store) -> None:
        """The claim that justifies the second stage existing at all."""
        pytest.importorskip("sentence_transformers", reason="needs the 'rerank' extra (torch)")
        from src.jobcore.rag.rerank import cross_encoder_scorer

        embed_only = sum(
            1
            for intent, expected in INTENTS
            if [h.source for h in skills_store.query(intent, collection="skills", k=1)] == [expected]
        )
        reranked = self._top1_with_rerank(skills_store, cross_encoder_scorer())

        assert reranked > embed_only, f"reranking did not improve top-1 ({embed_only} -> {reranked})"
