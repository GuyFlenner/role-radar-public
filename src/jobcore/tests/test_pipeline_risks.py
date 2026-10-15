"""Per-row risks — the "watch for" list.

A risk is a human judgment about *this* req ("legacy stack, probe career paths"). It protects the
row from ``prune`` for the same reason a channel does, but unlike an outcome it says nothing about
sibling reqs at the same company — so it must not reach ``prior_outcomes`` and must not move a
sibling into WATCH.
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
    pipeline.upsert(ledger, [_row(), _row(role="Backend Group Lead", job_id="p2")], TODAY)
    return ledger


def test_add_risk_round_trips(led):
    pipeline.add_risk(
        led,
        "payoneer:engineering-leader-modernization",
        text="Glassdoor mentions legacy stack and limited growth for eng leaders",
        source="glassdoor",
        today=TODAY,
    )

    risks = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")["risks"]
    assert risks == [
        {
            "date": TODAY,
            "text": "Glassdoor mentions legacy stack and limited growth for eng leaders",
            "source": "glassdoor",
        }
    ]


def test_risks_are_append_only(led):
    opp_id = "payoneer:engineering-leader-modernization"
    pipeline.add_risk(led, opp_id, text="acquisition may reshuffle the roadmap", today=TODAY)
    pipeline.add_risk(led, opp_id, text="comp friction in reviews", today=TODAY)

    assert len(pipeline._find(led, opp_id=opp_id)["risks"]) == 2


def test_empty_risk_text_is_rejected(led):
    with pytest.raises(ValueError, match="risk text"):
        pipeline.add_risk(led, "payoneer:engineering-leader-modernization", text="   ", today=TODAY)


def test_add_risk_on_a_missing_row_raises(led):
    with pytest.raises(KeyError):
        pipeline.add_risk(led, "nope:not-here", text="something", today=TODAY)


def test_risk_makes_a_row_unprunable(led):
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    assert pipeline._is_untouched(opp) is True

    pipeline.add_risk(led, opp["id"], text="ADINT/surveillance domain — ethical concern", today=TODAY)

    assert pipeline._is_untouched(opp) is False


def test_risk_does_not_put_sibling_rows_in_watch(led):
    sibling = "payoneer:backend-group-lead"
    before = {name for name, rows in pipeline.bucket(led).items() if any(o["id"] == sibling for o in rows)}

    pipeline.add_risk(led, "payoneer:engineering-leader-modernization", text="mixed reviews on mgmt", today=TODAY)

    after = {name for name, rows in pipeline.bucket(led).items() if any(o["id"] == sibling for o in rows)}
    assert after == before
    assert "watch" not in after


def test_risk_does_not_leak_into_prior_outcomes(led):
    pipeline.add_risk(led, "payoneer:engineering-leader-modernization", text="legacy stack", today=TODAY)

    assert pipeline.prior_outcomes(led, "payoneer", exclude_id="payoneer:backend-group-lead") == []


def test_validate_rejects_a_risk_with_no_text(led):
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    opp["risks"] = [{"date": TODAY, "text": "", "source": ""}]

    assert any("risk with no text" in p for p in pipeline.validate(led))


def test_validate_rejects_an_unparseable_risk_date(led):
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    opp["risks"] = [{"date": "someday", "text": "legacy stack", "source": ""}]

    assert any("risk date" in p for p in pipeline.validate(led))


def test_the_markdown_board_surfaces_a_watch_for_on_the_act_now_queue(led):
    """A risk that only ever appears in the browser is a note nobody re-reads before an interview."""
    pipeline.add_risk(
        led, "payoneer:engineering-leader-modernization", text="legacy stack per Glassdoor", source="glassdoor"
    )

    out = pipeline.render(led, today=TODAY)

    assert "Watch for, on the queue above" in out
    assert "legacy stack per Glassdoor" in out
    assert "(glassdoor)" in out


def test_the_markdown_board_omits_the_watch_for_block_when_there_are_none(led):
    assert "Watch for, on the queue above" not in pipeline.render(led, today=TODAY)


def test_a_row_without_the_risks_key_still_validates(led):
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    opp.pop("risks", None)

    assert pipeline.validate(led) == []
