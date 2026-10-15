"""Reranking — AC4.

The failure a cross-encoder exists to fix: a passage that shares surface tokens with
the query but is about something else outranks the passage that actually answers it.
A bi-encoder scores the two sides independently and cannot see that; a cross-encoder
scores the pair.

The scorer is injected, so this proves the *ordering contract* without downloading a
model — which is also why torch stays out of CI's install.
"""

from __future__ import annotations

import pytest

from src.jobcore.rag.rerank import RerankUnavailable, rerank
from src.jobcore.rag.store import Hit

QUERY = "how does the index stay fresh when a document changes"

# First-stage (bi-encoder-ish) order: the decoy wins on surface overlap.
DECOY = Hit(
    chunk_id="decoy.md:0",
    source="decoy.md",
    ordinal=0,
    text="the document index fresh changes how does a stay when",  # same tokens, no meaning
    score=0.95,
)
ANSWER = Hit(
    chunk_id="answer.md:3",
    source="answer.md",
    ordinal=3,
    text="re-ingestion deletes the chunks a source no longer produces, so orphans cannot be cited",
    score=0.42,
)
FILLER = [
    Hit(chunk_id=f"filler.md:{i}", source="filler.md", ordinal=i, text=f"unrelated passage {i}", score=0.30 - i / 100)
    for i in range(3)
]


def semantic_scorer(query: str, passage: str) -> float:
    """Stands in for a cross-encoder: rewards the passage that answers the question."""
    if "orphans cannot be cited" in passage:
        return 0.99
    if "unrelated" in passage:
        return 0.20
    return 0.10  # the decoy: lexically similar, semantically empty


class TestRerank:
    def test_decoy_ranks_first_before_reranking(self) -> None:
        """Establishes the premise — without this the test below proves nothing."""
        first_stage = sorted([DECOY, ANSWER, *FILLER], key=lambda h: -h.score)
        assert first_stage[0].chunk_id == "decoy.md:0"

    def test_reranking_demotes_the_decoy_out_of_the_top_three(self) -> None:
        reranked = rerank(QUERY, [DECOY, ANSWER, *FILLER], scorer=semantic_scorer, top_k=8)

        assert reranked[0].chunk_id == "answer.md:3", "the answering passage must win"
        top_three = [hit.chunk_id for hit in reranked[:3]]
        assert "decoy.md:0" not in top_three, f"decoy survived in the top-3: {top_three}"

    def test_every_hit_carries_its_rerank_score(self) -> None:
        """Both stages must remain visible so an order change can be explained."""
        reranked = rerank(QUERY, [DECOY, ANSWER], scorer=semantic_scorer, top_k=8)

        assert all(hit.rerank_score is not None for hit in reranked)
        assert all(hit.score > 0 for hit in reranked), "first-stage score must be preserved"

    def test_top_k_truncates(self) -> None:
        reranked = rerank(QUERY, [DECOY, ANSWER, *FILLER], scorer=semantic_scorer, top_k=2)
        assert len(reranked) == 2

    def test_empty_input_is_empty_output(self) -> None:
        assert rerank(QUERY, [], scorer=semantic_scorer) == []

    def test_ordering_is_reproducible_under_ties(self) -> None:
        """Ties break on chunk_id, so repeated runs cannot disagree."""
        tied = [
            Hit(chunk_id="b.md:0", source="b.md", ordinal=0, text="unrelated passage x", score=0.5),
            Hit(chunk_id="a.md:0", source="a.md", ordinal=0, text="unrelated passage y", score=0.5),
        ]
        first = [h.chunk_id for h in rerank(QUERY, tied, scorer=semantic_scorer)]
        second = [h.chunk_id for h in rerank(QUERY, list(reversed(tied)), scorer=semantic_scorer)]
        assert first == second == ["a.md:0", "b.md:0"]

    def test_missing_extra_raises_rather_than_degrading_silently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A reranker that quietly does nothing is the cov=n/a failure again."""
        import builtins

        real_import = builtins.__import__

        def blocked(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("sentence_transformers"):
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", blocked)

        from src.jobcore.rag.rerank import cross_encoder_scorer

        with pytest.raises(RerankUnavailable):
            cross_encoder_scorer()
