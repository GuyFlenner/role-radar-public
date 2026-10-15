"""Golden-smoke: end-to-end deterministic run of the shortlist pipeline.

Runs the real jobcore shortlist entrypoint (``build_shortlist``) — no mocks of
the core logic, no network, no LLM — against a small committed fixture CSV and
asserts the ranked output shape. The fixture uses empty date columns so the
stale-drop (relative to today) never fires, keeping this stable over time.
"""

from pathlib import Path

from src.jobcore.shortlist import build_shortlist

FIXTURE = Path(__file__).parent / "fixtures" / "Referally Leadership golden-smoke.csv"


def test_golden_smoke_shortlist_shape():
    jobs = build_shortlist(FIXTURE, from_scratch=True)

    # fixed number of ranked rows from the committed fixture (all pass relevance)
    assert len(jobs) == 4

    # required fields present and non-empty on every ranked row
    for j in jobs:
        assert j["company"]
        assert j["title"]
        assert "match_score" in j

    # scores within the valid 1..10 range
    assert all(1 <= j["match_score"] <= 10 for j in jobs)

    # ranked by match_score, descending
    scores = [j["match_score"] for j in jobs]
    assert scores == sorted(scores, reverse=True)

    # AI-leadership title floored high → top of the ranking
    assert jobs[0]["title"] == "LLM Team Lead"
    assert jobs[0]["match_score"] >= 8
