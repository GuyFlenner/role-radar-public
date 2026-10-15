"""Dated interview events — a timeline the ledger could not express before.

The load-bearing test here is :func:`test_event_does_not_put_sibling_rows_in_watch`. ``history`` is
not a general timeline: :func:`~src.jobcore.pipeline.bucket` sends a row to WATCH on *any* sibling
history, and :func:`~src.jobcore.pipeline._is_untouched` treats history as "a human acted". Recording
"HR screen on the 13th" in ``history`` would therefore drag every other req at that company into
WATCH and freeze them against ``prune`` — the same trap ``set_liveness`` documents in its docstring.
So events live in their own list, and these tests fail if anyone merges the two.
"""

import pytest

from src.jobcore import pipeline

TODAY = "2026-08-07"


def _row(company="Payoneer", role="Engineering Leader Modernization", job_id="p1"):
    return {
        "company": company,
        "title": role,
        "job_id": job_id,
        "url": f"https://example.test/{job_id}",
        "location": "Petah Tikva",
        "match_score": 8,
        "level_band": "in_band",
        "source": "linkedin",
    }


@pytest.fixture
def led():
    ledger = pipeline.load("nonexistent-for-tests.json")
    pipeline.upsert(
        ledger,
        [_row(), _row(role="Backend Group Lead", job_id="p2")],
        TODAY,
    )
    return ledger


def test_add_event_appends_a_dated_entry(led):
    pipeline.add_event(led, "payoneer:engineering-leader-modernization", "HR_SCREEN", note="Liat, 30m", today=TODAY)

    events = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")["events"]
    assert events == [{"date": TODAY, "event": "HR_SCREEN", "note": "Liat, 30m"}]


def test_events_are_append_only(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.add_event(led, opp_id, "HR_SCREEN", today=TODAY)
    pipeline.add_event(led, opp_id, "TECH_SCREEN", today="2026-08-09")

    assert [e["event"] for e in pipeline._find(led, opp_id=opp_id)["events"]] == ["HR_SCREEN", "TECH_SCREEN"]


def test_unknown_event_type_is_rejected(led):
    with pytest.raises(ValueError, match="unknown event"):
        pipeline.add_event(led, "payoneer:engineering-leader-modernization", "COFFEE_CHAT", today=TODAY)


def test_add_event_on_a_missing_row_raises(led):
    with pytest.raises(KeyError):
        pipeline.add_event(led, "nope:not-here", "HR_SCREEN", today=TODAY)


# --- the separation that makes the whole design safe -------------------------


def test_event_does_not_land_in_history(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.add_event(led, opp_id, "SYSTEM_DESIGN", today=TODAY)

    assert pipeline._find(led, opp_id=opp_id)["history"] == []


def test_event_does_not_put_sibling_rows_in_watch(led):
    """The regression this whole design exists to avoid.

    Two Payoneer reqs. Recording an interview round on one must not reclassify the other — an
    interview is not an outcome, and only outcomes carry across siblings.
    """
    sibling = "payoneer:backend-group-lead"
    before = {name for name, rows in pipeline.bucket(led).items() if any(o["id"] == sibling for o in rows)}

    pipeline.add_event(led, "payoneer:engineering-leader-modernization", "MANAGER_ROUND", today=TODAY)

    after = {name for name, rows in pipeline.bucket(led).items() if any(o["id"] == sibling for o in rows)}
    assert after == before
    assert "watch" not in after


def test_event_does_not_leak_into_prior_outcomes(led):
    pipeline.add_event(led, "payoneer:engineering-leader-modernization", "FINAL_ROUND", today=TODAY)

    assert pipeline.prior_outcomes(led, "payoneer", exclude_id="payoneer:backend-group-lead") == []


def test_an_outcome_still_reaches_prior_outcomes(led):
    """The counter-test: proves the sibling mechanism still works for real outcomes."""
    pipeline.set_stage(led, "payoneer:engineering-leader-modernization", "CLOSED", outcome="THEY_REJECTED", today=TODAY)

    carried = pipeline.prior_outcomes(led, "payoneer", exclude_id="payoneer:backend-group-lead")
    assert [e["event"] for e in carried] == ["THEY_REJECTED"]


def test_event_alone_does_not_make_a_row_unprunable(led):
    """An event is a record of what happened, not a decision to keep the row.

    ``prune`` protects rows a human acted on. An interview round is certainly human action, so this
    asserts the *opposite* of the risk case: recording a round DOES protect the row.
    """
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    assert pipeline._is_untouched(opp) is True

    pipeline.add_event(led, opp["id"], "HR_SCREEN", today=TODAY)

    assert pipeline._is_untouched(opp) is False


# --- applied date ------------------------------------------------------------


def test_set_stage_applied_records_one_event(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "APPLIED", today=TODAY)

    events = pipeline._find(led, opp_id=opp_id)["events"]
    assert [e["event"] for e in events] == ["APPLIED"]
    assert events[0]["date"] == TODAY


def test_set_stage_applied_is_idempotent(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "APPLIED", today=TODAY)
    pipeline.set_stage(led, opp_id, "APPLIED", today="2026-08-11")
    pipeline.set_stage(led, opp_id, "IN_PROCESS", today="2026-08-12")

    events = pipeline._find(led, opp_id=opp_id)["events"]
    assert [e["event"] for e in events] == ["APPLIED"]
    assert events[0]["date"] == TODAY, "the first applied date must survive later stage changes"


def test_applied_date_returns_the_recorded_applied_event(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "APPLIED", today=TODAY)
    opp = pipeline._find(led, opp_id=opp_id)

    assert pipeline.applied_date(opp) == TODAY


# --- the two honesty cases the Phase 6 review found --------------------------


def test_a_stage_jumped_straight_to_offer_records_no_applied_date(led):
    """An offer cannot precede an application.

    The first version fired on any IN_FLIGHT_STAGES entry, so a row taken straight to OFFER got an
    APPLIED event dated the day of the offer. `None` is the honest answer — the same reason
    UNVERIFIED is a distinct state from OPEN.
    """
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "OFFER", today=TODAY)
    opp = pipeline._find(led, opp_id=opp_id)

    assert opp["events"] == []
    assert pipeline.applied_date(opp) is None


def test_in_process_without_a_prior_applied_records_no_date(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "IN_PROCESS", today=TODAY)

    assert pipeline.applied_date(pipeline._find(led, opp_id=opp_id)) is None


def test_reapplying_after_a_close_records_a_second_applied_event(led):
    """A closed-then-reopened req is a NEW application; dating it by the original misleads."""
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "APPLIED", today=TODAY)
    pipeline.set_stage(led, opp_id, "CLOSED", outcome="NO_RESPONSE", today="2026-09-01")
    pipeline.set_stage(led, opp_id, "APPLIED", today="2026-10-15")

    opp = pipeline._find(led, opp_id=opp_id)
    assert [e["date"] for e in opp["events"] if e["event"] == "APPLIED"] == [TODAY, "2026-10-15"]
    assert pipeline.applied_date(opp) == "2026-10-15", "the live pursuit is dated by the latest send"


def test_applied_date_is_none_when_never_applied(led):
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")

    assert pipeline.applied_date(opp) is None


def test_stage_change_that_is_not_a_submission_records_nothing(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "RESEARCHED", today=TODAY)

    assert pipeline._find(led, opp_id=opp_id).get("events") == []


# --- the markdown board, not just the browser --------------------------------


def test_in_flight_lines_show_the_applied_date_and_last_round(led):
    """`events` was added to answer "when did I apply?" — leaving it browser-only left the
    artifact most people actually read unable to answer it."""
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "APPLIED", today=TODAY)
    pipeline.add_event(led, opp_id, "HR_SCREEN", note="Liat", today="2026-08-09")
    pipeline.add_risk(led, opp_id, text="legacy stack", today=TODAY)

    out = pipeline.render(led, today="2026-08-10")

    assert f"applied {TODAY}" in out
    assert "last: HR_SCREEN 2026-08-09" in out
    assert "⚠ 1 watch-for" in out


def test_an_in_flight_row_with_no_events_gets_no_progress_note(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "IN_PROCESS", today=TODAY)

    out = pipeline.render(led, today=TODAY)

    assert "applied " not in out
    assert "last: " not in out


def test_the_applied_event_itself_is_not_reported_as_the_last_round(led):
    """ "last: APPLIED" would be noise — the applied date is already its own field."""
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.set_stage(led, opp_id, "APPLIED", today=TODAY)

    assert "last: APPLIED" not in pipeline.render(led, today=TODAY)


# --- validation --------------------------------------------------------------


def test_validate_rejects_an_unknown_event_type(led):
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    opp["events"] = [{"date": TODAY, "event": "LUNCH", "note": ""}]

    assert any("invalid event" in p for p in pipeline.validate(led))


def test_validate_rejects_an_unparseable_event_date(led):
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    opp["events"] = [{"date": "last tuesday", "event": "HR_SCREEN", "note": ""}]

    assert any("event date" in p for p in pipeline.validate(led))


def test_a_row_without_the_events_key_still_validates(led):
    """115 real rows predate this field. Adding it must not invalidate the committed ledger."""
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    opp.pop("events", None)

    assert pipeline.validate(led) == []
