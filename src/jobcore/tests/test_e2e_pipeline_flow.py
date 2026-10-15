"""End-to-end test of the poll → gate → ledger → board flow. Offline: the board API and
the liveness checker are injected, so this makes no network calls and no model calls.

Exists because every defect fixed on 2026-08-05 lived in a *seam* between two stages that
each had passing unit tests: the relevance gate ran at intake but could not be re-run; the
identity key normalized punctuation but not spelling; the liveness checker's UNVERIFIED
verdict overwrote what the poll already knew. Unit tests per stage cannot catch that class
of bug. This walks one batch through every stage in order and asserts on the artifact a
human actually reads — the rendered board.
"""

import json
from datetime import date, timedelta

from src.jobcore import ats_shortlist, pipeline

TODAY = "2026-08-05"
# The poll's stale-drop uses the REAL clock, so a fixed posting date silently ages out of the
# window and every test below then asserts on an empty shortlist. Keep it relative to today.
_RECENT = (date.today() - timedelta(days=4)).isoformat()

GOOD = "AI Software Team Lead, Platform"
REJECTED_LEADER_SPELLING = "AI Software Team Leader, ML Platform"
REJECTED_LEAD_SPELLING = "AI Software Team Lead, ML Platform"
NOISE = (
    "Global Talent Acquisition Team Lead",  # business function
    "Analog Hardware Tech Lead",  # hardware design
    "Mechanical Team Leader - ATE Department",  # the `lead\b` vs "Leader" defect
    "IT Tech Lead",  # IT
    "Product Analyst Team Lead",  # product analytics
)


class _Live:
    def __init__(self, status):
        self.status = status


def _board_payload(titles):
    """A Greenhouse-shaped API response. Client-rendered apply pages are the norm, so the
    liveness checker below returns UNVERIFIED for all of them — the real Comeet situation."""
    return {
        "jobs": [
            {
                "title": t,
                "location": {"name": "Tel Aviv, Israel"},
                "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}",
                "updated_at": _RECENT,
                "content": "<p>Python backend microservices AI agent LLM team lead.</p>",
            }
            for i, t in enumerate(titles)
        ]
    }


def _config(tmp_path, company="Acme"):
    p = tmp_path / "ats_targets.json"
    p.write_text(json.dumps({"targets": [{"company": company, "ats": "greenhouse", "token": "acme"}]}), "utf-8")
    return p


def _poll(tmp_path, titles, status="UNVERIFIED"):
    return ats_shortlist.build_ats_shortlist(
        _config(tmp_path),
        from_scratch=True,
        seen_store=tmp_path / "seen.json",
        get_json=lambda url: _board_payload(titles),
        liveness_check=lambda u: _Live(status),
    )[0]


# --- stage 1: the gate must hold at intake -----------------------------------


def test_e2e_noise_never_reaches_the_shortlist(tmp_path):
    jobs = _poll(tmp_path, (GOOD, *NOISE))
    assert [j["title"] for j in jobs] == [GOOD], "only the engineering seat survives the relevance gate"


def test_e2e_api_sourced_rows_come_back_listed_not_unverified(tmp_path):
    jobs = _poll(tmp_path, (GOOD,))
    assert jobs[0]["liveness"] == "LISTED"


# --- stage 2: the poll writes the ledger, and a rejection survives it --------


def test_e2e_a_rejected_req_does_not_re_enter_as_new(tmp_path):
    """The Aidoc case, end to end. A rejection recorded under one spelling must absorb the
    poll's other spelling instead of letting the role reappear as a clean 8/10 lead."""
    ledger = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(ledger, [{"company": "Acme", "title": REJECTED_LEADER_SPELLING, "match_score": 8}], "2026-07-30")
    pipeline.set_stage(ledger, ledger["opportunities"][0]["id"], "CLOSED", "THEY_REJECTED", "auto-rejection email")

    jobs = _poll(tmp_path, (GOOD, REJECTED_LEAD_SPELLING))
    pipeline.upsert(ledger, jobs, TODAY)

    rejected = [o for o in ledger["opportunities"] if o.get("outcome") == "THEY_REJECTED"]
    assert len(rejected) == 1
    assert len(ledger["opportunities"]) == 2, "one good row + the single rejected row, not three"

    board = pipeline.render(ledger, today=TODAY)
    act_now = board.split("## ⏳ IN FLIGHT")[0]
    assert "ML Platform" not in act_now, "a rejected req must never be offered for tailoring again"

    # The sibling role is NOT dropped and NOT promoted: a rejection is per-req, not
    # per-company, so it surfaces in WATCH with the prior outcome attached and the decision
    # stays the human's. This is exactly how the three live Aidoc rows behaved on 2026-08-05.
    watch = board.split("## 👀 WATCH")[1].split("## 🔻")[0]
    assert GOOD in watch
    assert "THEY_REJECTED" in watch and "auto-rejection email" in watch
    assert "_Nothing queued" in act_now, "nothing is offered for tailoring at a company that just rejected him"


def test_e2e_ledger_stays_valid_and_the_board_marks_the_row_actionable(tmp_path):
    ledger = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(ledger, _poll(tmp_path, (GOOD,)), TODAY)

    assert pipeline.validate(ledger) == []
    assert pipeline.needs_eyeball(ledger["opportunities"][0]) is False
    assert "LISTED on the board's API" in pipeline.render(ledger, today=TODAY)


# --- stage 3: the cleanup commands are safe on a real-shaped ledger ----------


def test_e2e_prune_cleans_rows_that_leaked_before_the_gate_was_tightened(tmp_path):
    """Simulates the actual 08-05 situation: noise already in the ledger from an earlier,
    looser poll. prune must remove exactly the noise and leave the pursuit untouched."""
    ledger = pipeline.load(tmp_path / "l.json")
    for title in NOISE:
        pipeline.upsert(ledger, [{"company": "Acme", "title": title, "match_score": 5}], "2026-08-03")
    pipeline.upsert(ledger, _poll(tmp_path, (GOOD,)), TODAY)
    pipeline.set_stage(ledger, ledger["opportunities"][-1]["id"], "APPLIED", None, "sent")

    assert pipeline.prune(ledger) == len(NOISE)
    assert [o["role"] for o in ledger["opportunities"]] == [GOOD]
    assert ledger["opportunities"][0]["stage"] == "APPLIED", "prune never touches a pursuit"


def test_e2e_repeated_polls_are_idempotent(tmp_path):
    """Three identical polls must leave one row, one channel entry, and a clean ledger — the
    property that makes a nightly poll safe to run unattended."""
    ledger = pipeline.load(tmp_path / "l.json")
    for _ in range(3):
        pipeline.upsert(ledger, _poll(tmp_path, (GOOD,)), TODAY)

    assert len(ledger["opportunities"]) == 1
    assert len(ledger["opportunities"][0]["channels"]) == 1
    assert pipeline.validate(ledger) == []
    assert pipeline.dedupe(ledger) == 0


# --- stage 4: the guardrails fire on the flow's own output -------------------


def test_e2e_unsent_person_route_alarm_fires_on_a_polled_row(tmp_path):
    ledger = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(ledger, _poll(tmp_path, (GOOD,)), "2026-07-30")
    opp_id = ledger["opportunities"][0]["id"]
    pipeline.add_channel(ledger, opp_id, "head_hunter", "Noa", today="2026-07-30")

    assert "ROUTE OPENED, NOTHING SENT" in pipeline.render(ledger, today=TODAY)

    pipeline.record_submission(ledger, opp_id, "head_hunter", "Noa", today=TODAY)
    assert "ROUTE OPENED, NOTHING SENT" not in pipeline.render(ledger, today=TODAY)


def test_e2e_route_conflict_still_blocks_a_second_route(tmp_path):
    """The pre-existing guard must survive all of the above."""
    import pytest

    ledger = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(ledger, _poll(tmp_path, (GOOD,)), TODAY)
    opp_id = ledger["opportunities"][0]["id"]
    pipeline.add_channel(ledger, opp_id, "head_hunter", "Noa", today=TODAY)
    pipeline.record_submission(ledger, opp_id, "head_hunter", "Noa", today=TODAY)

    with pytest.raises(ValueError):
        pipeline.record_submission(ledger, opp_id, "friend_referral", "Someone", today=TODAY)
