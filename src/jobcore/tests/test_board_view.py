"""board_view tests — pure view-model construction, no socket, no network.

The view model is the whole contract between the ledger and the local dashboard. Every
classification here must come from ``pipeline.py``; these tests exist to prove the view
never invents a second opinion about which bucket a row belongs to.
"""

import pytest

from src.jobcore import board_view, pipeline

TODAY = "2026-08-06"


def _row(company="BlinkOps", role="Backend Team Lead", job_id="4408237940", score=8):
    """A row in shortlist shape (title/match_score), as the ATS poll emits it."""
    return {
        "company": company,
        "title": role,
        "job_id": job_id,
        "url": f"https://example.test/{job_id}",
        "location": "Tel Aviv",
        "match_score": score,
        "level_band": "in_band",
        "source": "linkedin",
    }


def _ledger(tmp_path, rows=None):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, rows if rows is not None else [_row()], TODAY)
    return led


# --- buckets: exactly one home per row ---------------------------------------


def test_every_opportunity_lands_in_exactly_one_bucket(tmp_path):
    led = _ledger(
        tmp_path,
        [
            _row("Armis", "AI Engineering Team Lead", "a1"),
            _row("Gong", "Manager AI Agentic", "g1"),
            _row("NICE", "Director of Engineering", "n1"),
        ],
    )
    led["opportunities"][2]["level_band"] = "above_band"
    pipeline.set_stage(led, "gong:manager-ai-agentic", stage="APPLIED", today=TODAY)

    v = board_view.view(led, today=TODAY)

    assert sum(v["counts"].values()) == len(led["opportunities"])
    seen = [c["bucket"] for c in v["cards"]]
    assert sorted(seen) == sorted(["act_now", "in_flight", "above_band"])


def test_counts_match_the_cards_in_each_bucket(tmp_path):
    led = _ledger(tmp_path, [_row("Armis", "AI TL", "a1"), _row("Cyera", "FS TL", "c1")])

    v = board_view.view(led, today=TODAY)

    for name, n in v["counts"].items():
        assert len([c for c in v["cards"] if c["bucket"] == name]) == n


def test_bucket_assignment_agrees_with_pipeline_bucket(tmp_path):
    """The view must not re-derive classification — it must match pipeline.bucket()."""
    led = _ledger(tmp_path, [_row("Armis", "AI TL", "a1"), _row("Gong", "AI Mgr", "g1")])
    pipeline.set_stage(led, "gong:ai-mgr", stage="IN_PROCESS", today=TODAY)

    v = board_view.view(led, today=TODAY)
    truth = {o["id"]: name for name, rows in pipeline.bucket(led).items() for o in rows}

    assert {c["id"]: c["bucket"] for c in v["cards"]} == truth


# --- alarms ------------------------------------------------------------------


def test_two_named_routes_raise_a_route_conflict_alarm(tmp_path):
    led = _ledger(tmp_path, [_row("Grip Security", "SW Eng TL", "g9")])
    oid = "grip-security:sw-eng-tl"
    pipeline.add_channel(led, opp_id=oid, channel="head_hunter", name="Noa", today=TODAY)
    pipeline.add_channel(led, opp_id=oid, channel="head_hunter", name="Chen", today=TODAY)

    alarms = board_view.view(led, today=TODAY)["alarms"]

    conflicts = [a for a in alarms if a["kind"] == "route_conflict"]
    assert [a["id"] for a in conflicts] == [oid]


def test_person_route_past_the_grace_window_raises_nothing_sent(tmp_path):
    led = _ledger(tmp_path, [_row("Axon", "Engineering Manager", "x1")])
    oid = "axon:engineering-manager"
    pipeline.add_channel(led, opp_id=oid, channel="head_hunter", name="Noa", today="2026-07-30")

    alarms = board_view.view(led, today=TODAY)["alarms"]

    sent = [a for a in alarms if a["kind"] == "route_opened_nothing_sent"]
    assert len(sent) == 1
    assert sent[0]["id"] == oid
    assert sent[0]["age_days"] == 7


def test_person_route_inside_the_grace_window_is_silent(tmp_path):
    led = _ledger(tmp_path, [_row("Axon", "Engineering Manager", "x1")])
    pipeline.add_channel(led, opp_id="axon:engineering-manager", channel="head_hunter", name="Noa", today=TODAY)

    alarms = board_view.view(led, today=TODAY)["alarms"]

    assert [a for a in alarms if a["kind"] == "route_opened_nothing_sent"] == []


def test_a_scan_route_never_raises_nothing_sent(tmp_path):
    """A scan finding a posting creates no decaying advantage — only a person does."""
    led = _ledger(tmp_path, [_row("Armis", "AI TL", "a1")])
    led["opportunities"][0]["channels"] = [{"channel": "ats_scan", "date": "2026-07-01"}]

    alarms = board_view.view(led, today=TODAY)["alarms"]

    assert [a for a in alarms if a["kind"] == "route_opened_nothing_sent"] == []


# --- WATCH carries its reason ------------------------------------------------


def test_a_prior_rejection_at_the_company_lands_in_watch_with_the_reason(tmp_path):
    led = _ledger(
        tmp_path,
        [_row("Aidoc", "AI Algorithm Tech Lead", "d1"), _row("Aidoc", "Backend Tech Lead", "d2")],
    )
    pipeline.set_stage(
        led,
        "aidoc:backend-tech-lead",
        stage="CLOSED",
        outcome="THEY_REJECTED",
        note="auto-rejection email",
        today=TODAY,
    )

    card = next(c for c in board_view.view(led, today=TODAY)["cards"] if c["id"] == "aidoc:ai-algorithm-tech-lead")

    assert card["bucket"] == "watch"
    assert any(h["event"] == "THEY_REJECTED" for h in card["prior_outcomes"])
    assert any("auto-rejection email" in h.get("note", "") for h in card["prior_outcomes"])


# --- card fields -------------------------------------------------------------


def test_commands_embed_the_rows_real_id(tmp_path):
    led = _ledger(tmp_path, [_row("Armis", "AI Engineering Team Lead", "a1")])

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["id"] == "armis:ai-engineering-team-lead"
    for cmd in card["commands"].values():
        assert "armis:ai-engineering-team-lead" in cmd
        assert cmd.startswith("uv run python -m src.jobcore.pipeline ")


def test_route_renders_the_resolved_head_hunter(tmp_path):
    led = _ledger(tmp_path, [_row("Sett", "Engineering TL", "s1")])
    pipeline.add_channel(
        led,
        opp_id="sett:engineering-tl",
        channel="head_hunter",
        name="Chen",
        today=TODAY,
        chosen=True,
    )

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["route"] == "head_hunter/Chen"


def test_an_unresolved_second_route_renders_as_multi_route(tmp_path):
    led = _ledger(tmp_path, [_row("Grip Security", "SW Eng TL", "g9")])
    pipeline.add_channel(led, opp_id="grip-security:sw-eng-tl", channel="head_hunter", name="Noa", today=TODAY)

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["route"] == pipeline.route_label(led["opportunities"][0])
    assert "multi-route" in card["route"]


def test_a_scan_only_row_renders_as_scan(tmp_path):
    led = _ledger(tmp_path, [_row("Armis", "AI TL", "a1")])

    assert board_view.view(led, today=TODAY)["cards"][0]["route"] == "scan"


def test_unverified_posting_is_flagged_for_an_eyeball(tmp_path):
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])
    led["opportunities"][0]["posting_live"] = "UNVERIFIED"

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["needs_eyeball"] is True


# --- validate surfaces rather than raises ------------------------------------


def test_a_clean_ledger_reports_ok(tmp_path):
    v = board_view.view(_ledger(tmp_path), today=TODAY)

    assert v["validate"] == {"ok": True, "problems": []}


def test_a_malformed_ledger_surfaces_problems_instead_of_raising(tmp_path):
    led = _ledger(tmp_path)
    led["opportunities"][0]["stage"] = "BOGUS_STAGE"

    v = board_view.view(led, today=TODAY)

    assert v["validate"]["ok"] is False
    assert v["validate"]["problems"]


def test_an_empty_ledger_renders_an_empty_board(tmp_path):
    v = board_view.view(pipeline.load(tmp_path / "nope.json"), today=TODAY)

    assert v["cards"] == []
    assert v["alarms"] == []
    assert sum(v["counts"].values()) == 0


def test_mtime_is_passed_through_for_the_refresh_poll(tmp_path):
    v = board_view.view(_ledger(tmp_path), today=TODAY, mtime=1754400000.0)

    assert v["ledger_mtime"] == 1754400000.0


def test_view_is_json_serialisable(tmp_path):
    import json

    json.dumps(board_view.view(_ledger(tmp_path), today=TODAY))


def test_view_does_not_mutate_the_ledger(tmp_path):
    import copy

    led = _ledger(tmp_path, [_row("Armis", "AI TL", "a1")])
    before = copy.deepcopy(led)

    board_view.view(led, today=TODAY)

    assert led == before


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))


# --- every card offers a way to check the posting in a browser ---------------


def test_a_row_with_a_url_opens_that_posting(tmp_path):
    led = _ledger(tmp_path, [_row("Armis", "AI Engineering Team Lead", "a1")])

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["open_kind"] != "search"
    assert card["open_url"] == "https://example.test/a1"


def test_a_row_with_no_url_falls_back_to_a_search_rather_than_a_dead_link(tmp_path):
    """28 of 110 real rows carry no URL. A button that goes nowhere is worse than none."""
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])
    led["opportunities"][0]["url"] = ""

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["open_kind"] == "search"
    assert "Buildots" in card["open_url"]
    assert "Future+Lead" in card["open_url"] or "Future%20Lead" in card["open_url"]


def test_the_search_fallback_is_url_encoded(tmp_path):
    led = _ledger(tmp_path, [_row("R&D Co", "Team Lead / Manager", "q1")])
    led["opportunities"][0]["url"] = ""

    url = board_view.view(led, today=TODAY)["cards"][0]["open_url"]

    assert url.startswith("https://")
    assert " " not in url
    assert "&D" not in url.split("?", 1)[1].split("&")[0] or "%26" in url


def test_every_card_can_be_opened(tmp_path):
    """No card may render an affordance that goes nowhere."""
    led = _ledger(
        tmp_path,
        [_row("Armis", "AI TL", "a1"), _row("Buildots", "Future Lead", "b1")],
    )
    led["opportunities"][1]["url"] = ""

    for card in board_view.view(led, today=TODAY)["cards"]:
        assert card["open_url"].startswith("https://")
        assert card["open_kind"] in ("open", "check", "closed", "search")


def test_a_closed_posting_never_says_apply(tmp_path):
    """Amdocs/AI Evangelist Lead is POSTING_CLOSED — offering 'apply' on it is a lie."""
    led = _ledger(tmp_path, [_row("Amdocs", "AI Evangelist Lead", "m1")])
    led["opportunities"][0]["posting_live"] = "CLOSED"

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert "apply" not in card["open_label"].lower()
    assert card["open_label"] == "View closed posting"


def test_a_closed_pursuit_never_says_apply(tmp_path):
    led = _ledger(tmp_path, [_row("Amdocs", "AI Evangelist Lead", "m1")])
    pipeline.set_stage(led, "amdocs:ai-evangelist-lead", stage="CLOSED", outcome="POSTING_CLOSED", today=TODAY)

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert "apply" not in card["open_label"].lower()


def test_an_unconfirmed_posting_asks_you_to_check_it(tmp_path):
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])
    led["opportunities"][0]["posting_live"] = "UNVERIFIED"

    assert board_view.view(led, today=TODAY)["cards"][0]["open_label"] == "Check status"


def test_a_confirmed_open_posting_offers_to_open_it(tmp_path):
    led = _ledger(tmp_path, [_row("Armis", "AI TL", "a1")])
    led["opportunities"][0]["posting_live"] = "OPEN"

    assert board_view.view(led, today=TODAY)["cards"][0]["open_label"] == "Open posting"


def test_a_row_with_no_url_is_labelled_a_search_not_a_posting(tmp_path):
    led = _ledger(tmp_path, [_row("Akeyless", "Future Lead", "k1")])
    led["opportunities"][0]["url"] = ""
    led["opportunities"][0]["posting_live"] = "OPEN"

    assert board_view.view(led, today=TODAY)["cards"][0]["open_label"] == "Search the web"


def test_no_card_in_the_real_ledger_offers_apply_on_a_dead_posting():
    """Regression against the live ledger, where the defect was spotted."""
    from pathlib import Path

    led = pipeline.load(Path("job-tracking/pipeline.json"))
    for card in board_view.view(led)["cards"]:
        dead = card["posting_live"] == "CLOSED" or card["stage"] == "CLOSED"
        if dead:
            assert "apply" not in card["open_label"].lower()


# --- recording what you saw --------------------------------------------------


def test_a_card_offers_a_command_for_every_liveness_verdict(tmp_path):
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])

    cmds = board_view.view(led, today=TODAY)["cards"][0]["commands"]

    for key in ("live_open", "live_closed", "live_listed"):
        assert key in cmds
        assert cmds[key].startswith("uv run python -m src.jobcore.pipeline liveness ")
        assert "buildots:future-lead" in cmds[key]


def test_the_liveness_commands_name_the_right_status(tmp_path):
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])

    cmds = board_view.view(led, today=TODAY)["cards"][0]["commands"]

    assert "--status OPEN" in cmds["live_open"]
    assert "--status CLOSED" in cmds["live_closed"]
    assert "--status LISTED" in cmds["live_listed"]


def test_an_unchecked_card_reports_no_receipt(tmp_path):
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])

    assert board_view.view(led, today=TODAY)["cards"][0]["checked"] is None


def test_a_checked_card_carries_who_checked_it_and_when(tmp_path):
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])
    pipeline.set_liveness(led, "buildots:future-lead", "OPEN", note="apply form live", today="2026-08-04")

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["checked"]["status"] == "OPEN"
    assert card["checked"]["date"] == "2026-08-04"
    assert card["checked"]["age_days"] == 2
    assert card["checked"]["note"] == "apply form live"


def test_a_hand_checked_open_posting_no_longer_asks_to_be_checked(tmp_path):
    """The whole point: verifying it by hand must retire the 'Check status' prompt."""
    led = _ledger(tmp_path, [_row("Buildots", "Future Lead", "b1")])
    pipeline.set_liveness(led, "buildots:future-lead", "OPEN", today=TODAY)

    card = board_view.view(led, today=TODAY)["cards"][0]

    assert card["needs_eyeball"] is False
    assert card["open_label"] == "Open posting"


def test_a_hand_check_settles_a_js_rendered_posting_too(tmp_path):
    """On JS_RENDERED_HOSTS, needs_eyeball() is True no matter what posting_live says, because no
    checker can read the page. That makes the prompt unclearable by any automated means — a human
    who opened it and read it is the only thing that can settle it, so it has to count."""
    led = _ledger(tmp_path, [_row("Amdocs", "AI Evangelist Lead", "m1")])
    led["opportunities"][0]["url"] = "https://jobs.amdocs.com/careers/job/563431010427112"
    led["opportunities"][0]["posting_live"] = "OPEN"
    assert pipeline.needs_eyeball(led["opportunities"][0]) is True

    pipeline.set_liveness(led, "amdocs:ai-evangelist-lead", "OPEN", today=TODAY)

    card = board_view.view(led, today=TODAY)["cards"][0]
    assert card["needs_eyeball"] is False
    assert card["open_label"] == "Open posting"


def test_a_hand_checked_closed_posting_still_reads_as_closed(tmp_path):
    led = _ledger(tmp_path, [_row("Amdocs", "AI Evangelist Lead", "m1")])
    led["opportunities"][0]["url"] = "https://jobs.amdocs.com/careers/job/563431010427112"

    pipeline.set_liveness(led, "amdocs:ai-evangelist-lead", "CLOSED", today=TODAY)

    assert board_view.view(led, today=TODAY)["cards"][0]["open_label"] == "View closed posting"


def test_a_stale_hand_check_stops_counting(tmp_path):
    """A verdict from months ago is not evidence about today's posting."""
    led = _ledger(tmp_path, [_row("Amdocs", "AI Evangelist Lead", "m1")])
    led["opportunities"][0]["url"] = "https://jobs.amdocs.com/careers/job/563431010427112"

    pipeline.set_liveness(led, "amdocs:ai-evangelist-lead", "OPEN", today="2026-05-01")

    assert pipeline.needs_eyeball(led["opportunities"][0], today=TODAY) is True
