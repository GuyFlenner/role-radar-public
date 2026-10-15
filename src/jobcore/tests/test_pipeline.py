"""pipeline ledger tests — offline, tmp_path ledgers, no network, no model calls."""

import json

import pytest

from src.jobcore import pipeline

TODAY = "2026-07-30"


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


# --- load / save -------------------------------------------------------------


def test_load_missing_file_returns_empty_skeleton(tmp_path):
    led = pipeline.load(tmp_path / "nope.json")
    assert led["opportunities"] == []
    assert led["company_policy"] == {}
    assert led["schema_version"] == pipeline.SCHEMA_VERSION


def test_save_then_load_roundtrips(tmp_path):
    p = tmp_path / "l.json"
    led = pipeline.load(p)
    pipeline.upsert(led, [_row()], TODAY)
    pipeline.save(led, p)
    assert pipeline.load(p)["opportunities"][0]["id"] == "blinkops:backend-team-lead"


# --- LISTED renders as actionable, not as doubtful ---------------------------


def test_listed_rows_do_not_need_an_eyeball_and_render_distinctly(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    row = _row("Cyera", "Full Stack Team Lead, AI Team", job_id="c9")
    row["liveness"] = "LISTED"
    pipeline.upsert(led, [row], TODAY)
    opp = led["opportunities"][0]

    assert opp["posting_live"] == "LISTED", "a conclusive poll reading must survive into the ledger"
    assert pipeline.needs_eyeball(opp) is False
    assert pipeline.validate(led) == []

    board = pipeline.render(led, today=TODAY)
    assert "LISTED on the board's API" in board
    assert "unconfirmed — open the link" not in board


def test_unverified_rows_still_need_an_eyeball(tmp_path):
    """The control for the test above — the upgrade must not make every row look confirmed."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Someone", "Backend Team Lead", job_id="u9")], TODAY)
    assert led["opportunities"][0]["posting_live"] == "UNVERIFIED"
    assert pipeline.needs_eyeball(led["opportunities"][0]) is True


# --- prune: re-apply the relevance gate to rows that already leaked ----------
# The gate ran only at intake, so tightening scoring.is_relevant could not clean the 34
# noise rows the 2026-08-05 poll had already written. A filter you cannot re-run is a
# filter that only ever protects the future.


def test_prune_removes_untouched_rows_that_no_longer_pass_the_gate(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("SolarEdge", "Mechanical Team Leader - ATE Department", job_id="m1")], TODAY)
    pipeline.upsert(led, [_row("Alice", "Global Talent Acquisition Team Lead", job_id="a1")], TODAY)
    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead", job_id="c1")], TODAY)

    assert pipeline.prune(led) == 2
    assert [o["role"] for o in led["opportunities"]] == ["Full Stack Team Lead"]


def test_prune_uses_the_stored_domain_not_just_the_title(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    row = _row("SolarEdge", "Software Team Lead", job_id="d1")
    row["domain"] = "R&D - Hardware"
    pipeline.upsert(led, [row], TODAY)
    assert led["opportunities"][0]["domain"] == "R&D - Hardware"
    assert pipeline.prune(led) == 1


def test_prune_never_touches_a_row_a_human_or_a_person_route_touched(tmp_path):
    """The safety property. Prune is only ever allowed to drop rows nobody has acted on —
    otherwise a scoring change could silently delete a live pursuit."""
    led = pipeline.load(tmp_path / "l.json")
    bad = "Mechanical Team Leader"
    for i, mutate in enumerate(
        (
            lambda o: o.update(stage="RESEARCHED"),
            lambda o: o.update(position_folder="positions/x"),
            lambda o: o.update(history=[{"date": TODAY, "stage": "NEW", "note": "looked at it"}]),
            lambda o: o.update(submissions=[{"date": TODAY, "channel": "manual", "name": "", "note": ""}]),
            lambda o: o.update(channels=[{"date": TODAY, "channel": "head_hunter", "name": "Noa", "note": ""}]),
        )
    ):
        pipeline.upsert(led, [_row(f"Co{i}", bad, job_id=f"j{i}")], TODAY)
        mutate(led["opportunities"][-1])

    before = len(led["opportunities"])
    assert pipeline.prune(led) == 0
    assert len(led["opportunities"]) == before


# --- route opened, nothing sent (the 2026-08-05 Axon loss) -------------------
# Axon 911 arrived by name from head-hunter Noa on 07-30, the CV + PDF were built the
# same day and passed every gate — and no submission was ever recorded. Six days later
# another candidate was at final HR. Nothing on the board said "a person handed you this
# and it is still sitting here", so the decay was invisible.


def _seed_person_route(led, opened, *, stage="RESEARCHED", company="Axon 911", sent=None):
    pipeline.upsert(led, [_row(company, "Engineering Manager", job_id="")], opened)
    opp = led["opportunities"][-1]
    opp["stage"] = stage
    opp["channels"] = [{"date": opened, "channel": "head_hunter", "name": "Noa", "note": ""}]
    opp["submissions"] = sent or []
    return opp


def test_unsent_person_routes_flags_a_lead_past_the_grace_period(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    _seed_person_route(led, "2026-07-30")
    flagged = pipeline.unsent_person_routes(led, today="2026-08-05")
    assert [o["company"] for o, _ in flagged] == ["Axon 911"]
    assert flagged[0][1] == 6, "age in days since the route opened"


def test_unsent_person_routes_respects_the_grace_period(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    _seed_person_route(led, "2026-08-04")
    assert pipeline.unsent_person_routes(led, today="2026-08-05") == []


def test_unsent_person_routes_ignores_sent_closed_and_scan_only(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    _seed_person_route(
        led,
        "2026-07-30",
        company="Sent Co",
        sent=[{"date": "2026-07-31", "channel": "head_hunter", "name": "Noa", "note": ""}],
    )
    _seed_person_route(led, "2026-07-30", company="Closed Co", stage="CLOSED")
    # A scan-sourced row is not a person's introduction — it must never appear here.
    pipeline.upsert(led, [_row("Scan Co", "Backend Team Lead", job_id="s1")], "2026-07-30")
    assert pipeline.unsent_person_routes(led, today="2026-08-05") == []


def test_board_renders_the_unsent_route_alarm(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    _seed_person_route(led, "2026-07-30")
    board = pipeline.render(led, today="2026-08-05")
    assert "ROUTE OPENED, NOTHING SENT" in board
    assert "head_hunter/Noa" in board
    assert "6 days" in board


def test_board_omits_the_alarm_when_every_route_is_actioned(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    _seed_person_route(
        led,
        "2026-07-30",
        sent=[{"date": "2026-07-31", "channel": "head_hunter", "name": "Noa", "note": ""}],
    )
    assert "ROUTE OPENED, NOTHING SENT" not in pipeline.render(led, today="2026-08-05")


# --- identity: punctuation must not fork a row -------------------------------
# The 2026-08-05 board carried Cyera "Full Stack Team Lead, AI Team" AND Cyera
# "Full Stack Team Lead - AI team" as two rows for one req. slugify() strips a comma
# but KEEPS a literal hyphen, then turns the spaces around it into hyphens too — so
# "Lead - AI" became "lead---ai" while "Lead, AI" became "lead-ai". Same req, two ids.


def test_slugify_collapses_hyphen_runs_so_punctuation_variants_agree():
    from src.jobcore.csv_ingest import slugify

    assert slugify("Full Stack Team Lead - AI team") == "full-stack-team-lead-ai-team"
    assert slugify("Full Stack Team Lead, AI Team") == "full-stack-team-lead-ai-team"
    assert slugify("Full Stack Team Lead — AI Team") == "full-stack-team-lead-ai-team"
    assert slugify("  Backend TL  ") == "backend-tl", "no leading/trailing hyphen"


def test_upsert_unifies_punctuation_variants_of_the_same_role(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead, AI Team", job_id="")], TODAY)
    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead - AI team", job_id="")], TODAY)
    assert len(led["opportunities"]) == 1, [o["id"] for o in led["opportunities"]]


def test_upsert_matches_a_legacy_hyphen_run_id_instead_of_forking(tmp_path):
    """Rows written before the slugify fix carry the old `lead---ai-team` id. The
    canonical-key fallback must recognise them, or the fix would itself fork every
    affected row one more time."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead - AI team", job_id="")], TODAY)
    led["opportunities"][0]["id"] = "cyera:full-stack-team-lead---ai-team"
    led["opportunities"][0]["stage"] = "APPLIED"

    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead, AI Team", job_id="")], TODAY)
    assert len(led["opportunities"]) == 1
    assert led["opportunities"][0]["stage"] == "APPLIED", "must update in place, not replace the decision"


def test_canon_key_is_blind_to_the_lead_leader_spelling(tmp_path):
    """The same class of bug as the EXCLUDE_TITLES `lead\\b` defect, one layer down.

    Aidoc rejected "AI Software Team **Leader**, ML Platform" on 2026-07-30. The 08-05
    poll returned "AI Software Team **Lead**, ML Platform" — the identical req, one letter
    apart — and it landed as a clean `NEW` 8/10 row while the rejection sat on a sibling
    row the board never connected to it. A rejected role was being re-offered.
    """
    assert pipeline.canon_key("Aidoc", "AI Software Team Leader, ML Platform") == pipeline.canon_key(
        "Aidoc", "AI Software Team Lead, ML Platform"
    )

    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Aidoc", "AI Software Team Leader, ML Platform", job_id="")], TODAY)
    led["opportunities"][0].update(stage="CLOSED", outcome="THEY_REJECTED")
    pipeline.upsert(led, [_row("Aidoc", "AI Software Team Lead, ML Platform", job_id="")], TODAY)

    assert len(led["opportunities"]) == 1
    assert led["opportunities"][0]["outcome"] == "THEY_REJECTED", "a rejection must not be laundered into NEW"


def test_canon_key_does_not_over_merge_on_the_word_leadership(tmp_path):
    """Normalization is token-level. 'Leadership' is a different word, not 'lead'."""
    assert pipeline.canon_key("X", "Leadership Program Manager") != pipeline.canon_key("X", "Lead Program Manager")


def test_upsert_still_separates_genuinely_different_roles(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead - AI team", job_id="")], TODAY)
    pipeline.upsert(led, [_row("Cyera", "AI Research Team Lead", job_id="")], TODAY)
    assert len(led["opportunities"]) == 2


def test_validate_reports_rows_that_share_a_canonical_key(tmp_path):
    """The gate half: a ledger that already contains a forked pair must fail validate,
    not merely stop growing."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead - AI team", job_id="")], TODAY)
    forked = json.loads(json.dumps(led["opportunities"][0]))
    forked["id"] = "cyera:full-stack-team-lead---ai-team"
    led["opportunities"].append(forked)

    problems = pipeline.validate(led)
    assert any("duplicate role" in p for p in problems), problems


def test_dedupe_merges_a_forked_pair_and_keeps_the_human_state(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Cyera", "Full Stack Team Lead - AI team", job_id="")], TODAY)
    forked = json.loads(json.dumps(led["opportunities"][0]))
    forked["id"] = "cyera:full-stack-team-lead---ai-team"
    forked["stage"] = "IN_PROCESS"
    forked["position_folder"] = "positions/cyera"
    forked["submissions"] = [{"date": TODAY, "channel": "head_hunter", "name": "Noa", "note": ""}]
    led["opportunities"].append(forked)

    merged = pipeline.dedupe(led)
    assert merged == 1
    assert len(led["opportunities"]) == 1
    kept = led["opportunities"][0]
    assert kept["stage"] == "IN_PROCESS", "the row carrying human state wins"
    assert kept["submissions"] and kept["position_folder"] == "positions/cyera"
    assert pipeline.validate(led) == []


# --- upsert ------------------------------------------------------------------


def test_upsert_inserts_then_is_idempotent(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    assert pipeline.upsert(led, [_row()], TODAY) == (1, 0)
    o = led["opportunities"][0]
    assert o["id"] == "blinkops:backend-team-lead"
    assert o["stage"] == "NEW"
    assert o["outcome"] is None
    assert o["posting_live"] == "UNVERIFIED"
    assert o["first_seen"] == TODAY
    assert o["history"] == []

    assert pipeline.upsert(led, [_row()], "2026-08-05") == (0, 1)
    assert len(led["opportunities"]) == 1
    assert led["opportunities"][0]["first_seen"] == TODAY, "first_seen is set once, never rewritten"
    assert led["opportunities"][0]["last_touched"] == "2026-08-05"


def test_upsert_never_clobbers_human_owned_fields(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(score=8)], TODAY)
    o = led["opportunities"][0]
    o["stage"] = "APPLIED"
    o["history"] = [{"date": "2026-07-25", "event": "APPLIED", "note": "via referral"}]
    o["position_folder"] = "positions/blinkops"
    o["posting_live"] = "OPEN"

    pipeline.upsert(led, [_row(score=6)], "2026-08-05")
    o = led["opportunities"][0]
    assert o["stage"] == "APPLIED", "tooling must never rewrite stage"
    assert len(o["history"]) == 1, "tooling must never rewrite history"
    assert o["position_folder"] == "positions/blinkops"
    assert o["posting_live"] == "OPEN", "a poll carrying no liveness reading must not blank it"
    assert o["score"] == 6, "tool-owned score DOES refresh"


def test_upsert_does_not_clobber_a_verified_score(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(score=5)], TODAY)
    o = led["opportunities"][0]
    o["score"] = 8
    o["score_verified"] = True
    o["score_note"] = "full JD dense with matching keywords the snippet never carried"

    pipeline.upsert(led, [_row(score=5)], "2026-08-05")
    o = led["opportunities"][0]
    assert o["score"] == 8, "a human-verified score must survive a poll that only saw the thin snippet"
    assert o["score_note"] == "full JD dense with matching keywords the snippet never carried"
    assert o["url"] == _row(score=5)["url"], "other tool-owned fields still refresh normally"


def test_upsert_matches_on_job_id_even_if_title_changes(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(role="Backend Team Lead")], TODAY)
    pipeline.upsert(led, [_row(role="Backend Team Lead (Core)")], TODAY)
    assert len(led["opportunities"]) == 1, "same job_id is the same opportunity"


def test_upsert_falls_back_to_company_role_when_no_job_id(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(job_id="")], TODAY)
    pipeline.upsert(led, [_row(job_id="")], TODAY)
    assert len(led["opportunities"]) == 1


def test_upsert_keeps_distinct_roles_at_the_same_company_separate(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="NorthWind", role="LLM Team Lead", job_id="a")], TODAY)
    pipeline.upsert(led, [_row(company="NorthWind", role="MLOps Team Lead", job_id="b")], TODAY)
    assert len(led["opportunities"]) == 2


# --- validate ----------------------------------------------------------------


def test_validate_clean_ledger_returns_no_problems(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    assert pipeline.validate(led) == []


def test_validate_rejects_outcome_without_closed_stage(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    led["opportunities"][0]["outcome"] = "THEY_REJECTED"  # stage is still NEW
    assert any("outcome" in p for p in pipeline.validate(led))


def test_validate_rejects_unknown_enum_values(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    led["opportunities"][0]["stage"] = "MAYBE"
    assert any("stage" in p for p in pipeline.validate(led))


def test_validate_detects_duplicate_ids(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    led["opportunities"].append(dict(led["opportunities"][0]))
    assert any("duplicate" in p.lower() for p in pipeline.validate(led))


# --- mutations ---------------------------------------------------------------


def test_set_stage_appends_history_when_outcome_given(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="NorthWind", role="LLM Team Lead")], TODAY)
    o = pipeline.set_stage(
        led,
        "northwind:llm-team-lead",
        "CLOSED",
        outcome="THEY_REJECTED",
        note="onsite: sys-design + code-review",
        today="2026-07-22",
    )
    assert o["stage"] == "CLOSED"
    assert o["outcome"] == "THEY_REJECTED"
    assert o["history"] == [
        {"date": "2026-07-22", "event": "THEY_REJECTED", "note": "onsite: sys-design + code-review"}
    ]


def test_set_stage_without_outcome_leaves_history_alone(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    o = pipeline.set_stage(led, "blinkops:backend-team-lead", "APPLIED", today=TODAY)
    assert o["stage"] == "APPLIED"
    assert o["history"] == []


def test_set_stage_rejects_outcome_without_closed(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    with pytest.raises(ValueError, match="CLOSED"):
        pipeline.set_stage(led, "blinkops:backend-team-lead", "APPLIED", outcome="THEY_REJECTED")


def test_set_stage_rejects_unknown_stage_and_outcome(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    with pytest.raises(ValueError, match="stage"):
        pipeline.set_stage(led, "blinkops:backend-team-lead", "MAYBE")
    with pytest.raises(ValueError, match="outcome"):
        pipeline.set_stage(led, "blinkops:backend-team-lead", "CLOSED", outcome="GHOSTED")


def test_set_stage_unknown_id_raises(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    with pytest.raises(KeyError):
        pipeline.set_stage(led, "nobody:nothing", "APPLIED")


# --- research_depth ------------------------------------------------------


def test_validate_accepts_missing_research_depth(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    assert pipeline.validate(led) == []


def test_validate_accepts_quick_and_deep(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    led["opportunities"][0]["research_depth"] = "deep"
    assert pipeline.validate(led) == []


def test_validate_rejects_invalid_research_depth(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    led["opportunities"][0]["research_depth"] = "medium"
    problems = pipeline.validate(led)
    assert any("research_depth" in p for p in problems)


def test_cli_set_research_depth_updates_field(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(
        ["add", "--ledger", str(led), "--company", "Acme", "--role", "Backend TL", "--url", "u", "--today", TODAY]
    )
    assert (
        pipeline.main(
            [
                "set",
                "acme:backend-tl",
                "--ledger",
                str(led),
                "--stage",
                "RESEARCHED",
                "--research-depth",
                "deep",
                "--today",
                TODAY,
            ]
        )
        == 0
    )
    assert pipeline.load(led)["opportunities"][0]["research_depth"] == "deep"


def test_cli_set_without_research_depth_leaves_field_unset(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(
        ["add", "--ledger", str(led), "--company", "Acme", "--role", "Backend TL", "--url", "u", "--today", TODAY]
    )
    pipeline.main(["set", "acme:backend-tl", "--ledger", str(led), "--stage", "APPLIED", "--today", TODAY])
    assert "research_depth" not in pipeline.load(led)["opportunities"][0]


# --- depth-escalation suggestion ------------------------------------------


def test_cli_event_suggests_deep_prep_when_signal_event_and_still_quick(tmp_path, capsys):
    led = tmp_path / "l.json"
    pipeline.main(
        ["add", "--ledger", str(led), "--company", "Acme", "--role", "Backend TL", "--url", "u", "--today", TODAY]
    )
    capsys.readouterr()
    rc = pipeline.main(["event", "acme:backend-tl", "--ledger", str(led), "--type", "HR_SCREEN", "--today", TODAY])
    assert rc == 0
    err = capsys.readouterr().err
    assert "research_depth is still 'quick'" in err
    assert "acme:backend-tl" in err
    assert "--research-depth deep" in err


def test_cli_event_suggestion_is_actually_runnable_as_printed(tmp_path, capsys):
    """Regression guard: the depth-escalation suggestion the pipeline prints must be a
    command that actually runs, not just a plausible-looking string. `set` requires
    --stage (it's required=True on that subparser) — a suggestion that omits it would
    fail with "the following arguments are required: --stage" if copy-pasted verbatim."""
    led = tmp_path / "l.json"
    pipeline.main(
        ["add", "--ledger", str(led), "--company", "Acme", "--role", "Backend TL", "--url", "u", "--today", TODAY]
    )
    capsys.readouterr()
    rc = pipeline.main(["event", "acme:backend-tl", "--ledger", str(led), "--type", "HR_SCREEN", "--today", TODAY])
    assert rc == 0
    err = capsys.readouterr().err

    suggestion_line = next(line for line in err.splitlines() if "then:" in line)
    printed_cmd = suggestion_line.split("then:", 1)[1].strip()
    assert printed_cmd.startswith("uv run python -m src.jobcore.pipeline ")
    printed_args = printed_cmd[len("uv run python -m src.jobcore.pipeline ") :].split()

    # Copy-paste the printed args verbatim. Only addition: point --ledger at this
    # test's tmp ledger instead of the real one, so the test never touches the real
    # job-tracking/pipeline.json — everything else is exactly what was printed.
    assert pipeline.main(printed_args + ["--ledger", str(led), "--today", TODAY]) == 0


def test_cli_event_no_suggestion_when_already_deep(tmp_path, capsys):
    led = tmp_path / "l.json"
    pipeline.main(
        ["add", "--ledger", str(led), "--company", "Acme", "--role", "Backend TL", "--url", "u", "--today", TODAY]
    )
    pipeline.main(
        [
            "set",
            "acme:backend-tl",
            "--ledger",
            str(led),
            "--stage",
            "RESEARCHED",
            "--research-depth",
            "deep",
            "--today",
            TODAY,
        ]
    )
    capsys.readouterr()
    pipeline.main(["event", "acme:backend-tl", "--ledger", str(led), "--type", "HR_SCREEN", "--today", TODAY])
    err = capsys.readouterr().err
    assert "research_depth" not in err


def test_cli_event_no_suggestion_for_non_signal_event(tmp_path, capsys):
    led = tmp_path / "l.json"
    pipeline.main(
        ["add", "--ledger", str(led), "--company", "Acme", "--role", "Backend TL", "--url", "u", "--today", TODAY]
    )
    capsys.readouterr()
    pipeline.main(["event", "acme:backend-tl", "--ledger", str(led), "--type", "APPLIED", "--today", TODAY])
    err = capsys.readouterr().err
    assert "research_depth" not in err


def test_interview_signal_events_excludes_terminal_events():
    assert pipeline.INTERVIEW_SIGNAL_EVENTS == set(pipeline.EVENTS) - {
        "APPLIED",
        "OFFER_RECEIVED",
        "FOLLOW_UP_SENT",
    }


# --- company policy / suppression -------------------------------------------


def test_policy_suppresses_every_row_at_that_company(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="JFrog", role="R&D Team Leader", job_id="a")], TODAY)
    pipeline.upsert(led, [_row(company="JFrog", role="R&D Team Lead Security", job_id="b")], TODAY)
    pipeline.set_policy(led, "jfrog", "do_not_pursue", "auto-rejected top-of-funnel", TODAY)
    assert all(pipeline.is_suppressed(led, o) for o in led["opportunities"])


def test_unpolicied_company_is_not_suppressed(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    assert not pipeline.is_suppressed(led, led["opportunities"][0])


def test_prior_outcomes_finds_sibling_rejection_at_same_company(tmp_path):
    """A different req at a company that rejected you surfaces WITH its history attached."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="NorthWind", role="LLM Team Lead", job_id="a")], TODAY)
    pipeline.upsert(led, [_row(company="NorthWind", role="MLOps Team Lead", job_id="b")], TODAY)
    pipeline.set_stage(
        led, "northwind:llm-team-lead", "CLOSED", outcome="THEY_REJECTED", note="onsite", today="2026-07-22"
    )

    prior = pipeline.prior_outcomes(led, "northwind", exclude_id="northwind:mlops-team-lead")
    assert len(prior) == 1
    assert prior[0]["event"] == "THEY_REJECTED"

    sibling = next(o for o in led["opportunities"] if o["id"] == "northwind:mlops-team-lead")
    assert sibling["stage"] == "NEW", "a sibling req is NOT auto-closed by the other's rejection"


def test_prior_outcomes_excludes_the_row_itself(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="NorthWind", role="LLM Team Lead")], TODAY)
    pipeline.set_stage(led, "northwind:llm-team-lead", "CLOSED", outcome="THEY_REJECTED", today=TODAY)
    assert pipeline.prior_outcomes(led, "northwind", exclude_id="northwind:llm-team-lead") == []


# --- bucketing ---------------------------------------------------------------


def test_bucket_routes_rows_to_the_right_sections(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [
            _row(company="BlinkOps", role="Backend TL", job_id="1", score=8),
            _row(company="NICE", role="Director of Engineering", job_id="2", score=8),
            _row(company="Bluebird", role="Backend AI TL", job_id="3", score=9),
            _row(company="Intango", role="EM Brand Advertising", job_id="4", score=6),
            _row(company="JFrog", role="R&D Team Leader", job_id="5", score=5),
        ],
        TODAY,
    )
    for o in led["opportunities"]:
        o["posting_live"] = "OPEN"
    next(o for o in led["opportunities"] if o["company"] == "NICE")["level_band"] = "above_band"
    pipeline.set_stage(led, "bluebird:backend-ai-tl", "IN_PROCESS", today=TODAY)
    pipeline.set_stage(
        led, "intango:em-brand-advertising", "CLOSED", outcome="THEY_REJECTED", note="auto-reject", today=TODAY
    )
    pipeline.set_policy(led, "jfrog", "do_not_pursue", "auto-rejected", TODAY)

    b = pipeline.bucket(led)
    assert [o["company"] for o in b["act_now"]] == ["BlinkOps"]
    assert [o["company"] for o in b["in_flight"]] == ["Bluebird"]
    assert [o["company"] for o in b["above_band"]] == ["NICE"]
    assert [o["company"] for o in b["closed"]] == ["Intango"]
    assert [o["company"] for o in b["suppressed"]] == ["JFrog"]


def test_rejected_row_never_appears_in_act_now(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="NorthWind", role="LLM Team Lead")], TODAY)
    led["opportunities"][0]["posting_live"] = "OPEN"
    pipeline.set_stage(led, "northwind:llm-team-lead", "CLOSED", outcome="THEY_REJECTED", today=TODAY)
    assert pipeline.bucket(led)["act_now"] == []


def test_suppressed_company_beats_every_other_bucket(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="JFrog", role="R&D TL")], TODAY)
    led["opportunities"][0]["posting_live"] = "OPEN"
    pipeline.set_policy(led, "jfrog", "do_not_pursue", "closed chapter", TODAY)
    b = pipeline.bucket(led)
    assert b["act_now"] == [] and b["watch"] == []
    assert [o["company"] for o in b["suppressed"]] == ["JFrog"]


def test_unverified_posting_still_reaches_act_now(tmp_path):
    """LinkedIn guest liveness can almost never confirm OPEN, so UNVERIFIED is the norm.
    Treating it as suspicious emptied ACT NOW entirely against the real 55-row ledger."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)  # posting_live defaults to UNVERIFIED
    b = pipeline.bucket(led)
    assert [o["company"] for o in b["act_now"]] == ["BlinkOps"]
    assert b["watch"] == []


def test_dead_posting_moves_an_unstarted_role_to_watch(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    led["opportunities"][0]["posting_live"] = "CLOSED"
    b = pipeline.bucket(led)
    assert b["act_now"] == []
    assert [o["company"] for o in b["watch"]] == ["BlinkOps"]


def test_act_now_sorted_by_score_desc(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [
            _row(company="Low", role="Backend TL", job_id="1", score=5),
            _row(company="High", role="Backend TL", job_id="2", score=9),
        ],
        TODAY,
    )
    for o in led["opportunities"]:
        o["posting_live"] = "OPEN"
    assert [o["company"] for o in pipeline.bucket(led)["act_now"]] == ["High", "Low"]


# --- render ------------------------------------------------------------------


def test_render_includes_all_sections_and_suppression_reason(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="JFrog", role="R&D Team Leader")], TODAY)
    pipeline.set_policy(led, "jfrog", "do_not_pursue", "auto-rejected top-of-funnel", TODAY)
    md = pipeline.render(led, TODAY)
    assert "# Pipeline Board" in md
    assert "ACT NOW" in md
    assert "auto-rejected top-of-funnel" in md
    assert md.endswith("\n")


def test_render_annotates_watch_rows_with_prior_outcome(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="NorthWind", role="LLM Team Lead", job_id="a")], TODAY)
    pipeline.upsert(led, [_row(company="NorthWind", role="MLOps Team Lead", job_id="b")], TODAY)
    pipeline.set_stage(
        led, "northwind:llm-team-lead", "CLOSED", outcome="THEY_REJECTED", note="onsite", today="2026-07-22"
    )
    md = pipeline.render(led, TODAY)
    assert "THEY_REJECTED" in md
    assert "2026-07-22" in md


def test_render_empty_ledger_does_not_crash(tmp_path):
    md = pipeline.render(pipeline.load(tmp_path / "l.json"), TODAY)
    assert "# Pipeline Board" in md


# --- CLI ---------------------------------------------------------------------


def test_cli_add_then_render_roundtrip(tmp_path):
    led = tmp_path / "l.json"
    board = tmp_path / "BOARD.md"
    rc = pipeline.main(
        [
            "add",
            "--ledger",
            str(led),
            "--company",
            "BlinkOps",
            "--role",
            "Backend Team Lead",
            "--url",
            "https://example.test/1",
            "--score",
            "8",
            "--today",
            TODAY,
        ]
    )
    assert rc == 0
    assert pipeline.main(["render", "--ledger", str(led), "--out", str(board), "--today", TODAY]) == 0
    assert "BlinkOps" in board.read_text(encoding="utf-8")


def test_cli_add_infers_level_band_from_title(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(
        [
            "add",
            "--ledger",
            str(led),
            "--company",
            "NICE",
            "--role",
            "Director of Engineering",
            "--url",
            "u",
            "--today",
            TODAY,
        ]
    )
    assert pipeline.load(led)["opportunities"][0]["level_band"] == "above_band"


def test_cli_set_stage_persists(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(
        [
            "add",
            "--ledger",
            str(led),
            "--company",
            "Sett",
            "--role",
            "Engineering TL",
            "--url",
            "https://example.test/2",
            "--today",
            TODAY,
        ]
    )
    assert (
        pipeline.main(["set", "sett:engineering-tl", "--ledger", str(led), "--stage", "APPLIED", "--today", TODAY]) == 0
    )
    assert pipeline.load(led)["opportunities"][0]["stage"] == "APPLIED"


def test_cli_set_invalid_transition_returns_nonzero(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(["add", "--ledger", str(led), "--company", "X", "--role", "Y", "--url", "u", "--today", TODAY])
    rc = pipeline.main(
        ["set", "x:y", "--ledger", str(led), "--stage", "APPLIED", "--outcome", "THEY_REJECTED", "--today", TODAY]
    )
    assert rc == 1


def test_cli_policy_persists(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(
        ["add", "--ledger", str(led), "--company", "JFrog", "--role", "R&D TL", "--url", "u", "--today", TODAY]
    )
    assert (
        pipeline.main(
            ["policy", "jfrog", "--ledger", str(led), "--do-not-pursue", "--reason", "closed", "--today", TODAY]
        )
        == 0
    )
    assert pipeline.load(led)["company_policy"]["jfrog"]["policy"] == "do_not_pursue"


def test_cli_validate_returns_nonzero_on_broken_ledger(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(["add", "--ledger", str(led), "--company", "X", "--role", "Y", "--url", "u", "--today", TODAY])
    data = pipeline.load(led)
    data["opportunities"][0]["stage"] = "BOGUS"
    pipeline.save(data, led)
    assert pipeline.main(["validate", "--ledger", str(led)]) == 1


def test_cli_validate_returns_zero_on_clean_ledger(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(["add", "--ledger", str(led), "--company", "X", "--role", "Y", "--url", "u", "--today", TODAY])
    assert pipeline.main(["validate", "--ledger", str(led)]) == 0


def test_cli_upsert_from_shortlist_json(tmp_path):
    src = tmp_path / "shortlist.json"
    src.write_text(json.dumps([_row()]), encoding="utf-8")
    led = tmp_path / "l.json"
    assert pipeline.main(["upsert", "--from", str(src), "--ledger", str(led), "--today", TODAY]) == 0
    assert pipeline.load(led)["opportunities"][0]["company"] == "BlinkOps"


def test_render_collapses_the_act_now_tail(tmp_path):
    """The board is a decision queue, not an inventory dump."""
    led = pipeline.load(tmp_path / "l.json")
    n = pipeline.ACT_NOW_LIMIT + 5
    pipeline.upsert(
        led,
        [_row(company=f"Co{i:02d}", role="Backend TL", job_id=str(i), score=9) for i in range(n)],
        TODAY,
    )
    assert len(pipeline.bucket(led)["act_now"]) == n
    md = pipeline.render(led, TODAY)
    assert md.count("| 9/10 |") == pipeline.ACT_NOW_LIMIT
    assert "+5 more un-started in-band roles" in md


# --- provenance: routes in, and the double-submission guard --------------------


def test_upsert_records_the_arrival_channel(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY, channel="job_radar_delta")
    ch = led["opportunities"][0]["channels"]
    assert [c["channel"] for c in ch] == ["job_radar_delta"]
    assert ch[0]["date"] == TODAY


def test_repeat_scans_do_not_duplicate_the_channel_entry(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    for _ in range(3):
        pipeline.upsert(led, [_row()], TODAY, channel="ats_scan")
    assert len(led["opportunities"][0]["channels"]) == 1


def test_posted_date_is_kept_separate_from_first_seen(tmp_path):
    """When the JD went live on the board vs when it reached you are different facts."""
    led = pipeline.load(tmp_path / "l.json")
    row = _row() | {"posted_str": "2026-07-14"}
    pipeline.upsert(led, [row], TODAY)
    o = led["opportunities"][0]
    assert o["posted_date"] == "2026-07-14"
    assert o["first_seen"] == TODAY


def test_attributed_channel_requires_a_name(tmp_path):
    """An unnamed head-hunter is the exact ambiguity that causes a double submission."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    with pytest.raises(ValueError, match="name"):
        pipeline.add_channel(led, "blinkops:backend-team-lead", "head_hunter", name="")


def test_add_channel_rejects_unknown_channel(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    with pytest.raises(ValueError, match="channel"):
        pipeline.add_channel(led, "blinkops:backend-team-lead", "carrier_pigeon", name="x")


def test_route_conflict_flags_the_grip_case(tmp_path):
    """In the pipeline from a scan, then pitched by a head-hunter days later."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Grip Security", role="SW Eng TL")], "2026-07-25", channel="job_radar_delta")
    opp = led["opportunities"][0]
    assert not pipeline.route_conflict(opp), "a single scan route is not a conflict"

    pipeline.add_channel(led, "grip-security:sw-eng-tl", "head_hunter", name="Noa", today="2026-07-30")
    assert pipeline.route_conflict(opp), "scan + head-hunter IS a conflict"


def test_two_head_hunters_on_one_req_is_a_conflict(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Grip Security", role="SW Eng TL")], TODAY, channel="")
    pipeline.add_channel(led, "grip-security:sw-eng-tl", "head_hunter", name="Noa", today="2026-07-30")
    pipeline.add_channel(led, "grip-security:sw-eng-tl", "head_hunter", name="Other Agency", today="2026-07-25")
    assert pipeline.route_conflict(led["opportunities"][0])


def test_second_submission_via_a_different_route_is_blocked(tmp_path):
    """The whole point: never send the same CV through two routes."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Grip Security", role="SW Eng TL")], TODAY)
    pipeline.record_submission(led, "grip-security:sw-eng-tl", "head_hunter", name="Noa", today="2026-07-31")

    with pytest.raises(ValueError, match="already submitted"):
        pipeline.record_submission(led, "grip-security:sw-eng-tl", "direct_outreach", today="2026-08-01")
    assert len(led["opportunities"][0]["submissions"]) == 1


def test_resubmitting_the_same_route_is_idempotent_not_an_error(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    pipeline.record_submission(led, "blinkops:backend-team-lead", "head_hunter", name="Noa", today=TODAY)
    pipeline.record_submission(led, "blinkops:backend-team-lead", "head_hunter", name="Noa", today=TODAY)
    assert len(led["opportunities"][0]["submissions"]) == 1


def test_force_allows_a_deliberate_second_route(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    pipeline.record_submission(led, "blinkops:backend-team-lead", "head_hunter", name="Noa", today=TODAY)
    pipeline.record_submission(led, "blinkops:backend-team-lead", "direct_outreach", today=TODAY, force=True)
    assert len(led["opportunities"][0]["submissions"]) == 2


def test_validate_flags_multi_route_submissions(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    pipeline.record_submission(led, "blinkops:backend-team-lead", "head_hunter", name="Noa", today=TODAY)
    pipeline.record_submission(led, "blinkops:backend-team-lead", "direct_outreach", today=TODAY, force=True)
    assert any("different routes" in p for p in pipeline.validate(led))


def test_render_surfaces_the_route_conflict_section(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Grip Security", role="SW Eng TL")], "2026-07-25", channel="job_radar_delta")
    pipeline.add_channel(led, "grip-security:sw-eng-tl", "head_hunter", name="Noa", today="2026-07-30")
    md = pipeline.render(led, TODAY)
    assert "RESOLVE THE ROUTE FIRST" in md
    assert "Noa" in md
    assert "not yet sent" in md


def test_render_conflict_section_absent_when_single_route(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY, channel="ats_scan")
    assert "RESOLVE THE ROUTE FIRST" not in pipeline.render(led, TODAY)


def test_cli_channel_then_submit_blocks_the_second_route(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(
        [
            "add",
            "--ledger",
            str(led),
            "--company",
            "Grip Security",
            "--role",
            "SW Eng TL",
            "--url",
            "u",
            "--today",
            TODAY,
        ]
    )
    assert (
        pipeline.main(
            [
                "channel",
                "grip-security:sw-eng-tl",
                "--ledger",
                str(led),
                "--channel",
                "head_hunter",
                "--name",
                "Noa",
                "--today",
                TODAY,
            ]
        )
        == 0
    )
    assert (
        pipeline.main(
            [
                "submit",
                "grip-security:sw-eng-tl",
                "--ledger",
                str(led),
                "--channel",
                "head_hunter",
                "--name",
                "Noa",
                "--today",
                TODAY,
            ]
        )
        == 0
    )
    # A different route must be refused, non-zero, and must not be written.
    assert (
        pipeline.main(
            [
                "submit",
                "grip-security:sw-eng-tl",
                "--ledger",
                str(led),
                "--channel",
                "direct_outreach",
                "--today",
                TODAY,
            ]
        )
        == 1
    )
    assert len(pipeline.load(led)["opportunities"][0]["submissions"]) == 1


def test_cli_channel_rejects_unnamed_head_hunter(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(["add", "--ledger", str(led), "--company", "X", "--role", "Y", "--url", "u", "--today", TODAY])
    assert pipeline.main(["channel", "x:y", "--ledger", str(led), "--channel", "head_hunter", "--today", TODAY]) == 1


def test_posted_date_refreshes_on_update_not_just_insert(tmp_path):
    """posted_date must be in _TOOL_FIELDS — it was in _normalize only, so existing rows
    seeded before the field existed never picked it up."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)
    assert led["opportunities"][0]["posted_date"] == ""
    pipeline.upsert(led, [_row() | {"posted_str": "2026-07-06"}], TODAY)
    assert led["opportunities"][0]["posted_date"] == "2026-07-06"


def test_choosing_a_route_clears_the_conflict(tmp_path):
    """Resolving an ambiguity is a real event — Grip's turned out to be a forwarded unsigned
    email, so there was no competing claim. Without recording that, the board nags forever."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Grip Security", role="SW Eng TL")], "2026-07-25", channel="job_radar_delta")
    pipeline.add_channel(led, "grip-security:sw-eng-tl", "head_hunter", name="Noa", today="2026-07-30")
    opp = led["opportunities"][0]
    assert pipeline.route_conflict(opp)

    pipeline.add_channel(led, "grip-security:sw-eng-tl", "head_hunter", name="Noa", today="2026-07-30", chosen=True)
    assert not pipeline.route_conflict(opp)
    assert pipeline.chosen_route(opp)["name"] == "Noa"
    assert len(opp["channels"]) == 2, "choosing must not duplicate the channel entry"


def test_only_one_route_can_be_chosen(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY, channel="ats_scan")
    pipeline.add_channel(led, "blinkops:backend-team-lead", "head_hunter", name="Noa", today=TODAY, chosen=True)
    pipeline.add_channel(led, "blinkops:backend-team-lead", "friend_referral", name="Dana", today=TODAY, chosen=True)
    opp = led["opportunities"][0]
    assert sum(1 for c in opp["channels"] if c.get("chosen")) == 1
    assert pipeline.chosen_route(opp)["name"] == "Dana"


def test_chosen_route_row_is_absent_from_the_board_conflict_section(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Grip Security", role="SW Eng TL")], TODAY, channel="job_radar_delta")
    pipeline.add_channel(led, "grip-security:sw-eng-tl", "head_hunter", name="Noa", today=TODAY, chosen=True)
    assert "RESOLVE THE ROUTE FIRST" not in pipeline.render(led, TODAY)


def test_cli_channel_chosen_flag(tmp_path):
    led = tmp_path / "l.json"
    pipeline.main(
        [
            "add",
            "--ledger",
            str(led),
            "--company",
            "Grip Security",
            "--role",
            "SW Eng TL",
            "--url",
            "u",
            "--today",
            TODAY,
        ]
    )
    assert (
        pipeline.main(
            [
                "channel",
                "grip-security:sw-eng-tl",
                "--ledger",
                str(led),
                "--channel",
                "head_hunter",
                "--name",
                "Noa",
                "--chosen",
                "--today",
                TODAY,
            ]
        )
        == 0
    )
    opp = pipeline.load(led)["opportunities"][0]
    assert pipeline.chosen_route(opp)["name"] == "Noa"


# --- verification honesty (the Amdocs drift) ---------------------------------


def test_js_rendered_host_always_needs_an_eyeball(tmp_path):
    """Amdocs renders apply-status client-side AND embeds decoy ATS labels; neither a direct
    fetch nor an Exa mirror could tell open from closed on 2026-07-30."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Amdocs", role="AI Evangelist Lead")], TODAY)
    o = led["opportunities"][0]
    o["url"] = "https://jobs.amdocs.com/careers/job/563431010427112"
    o["posting_live"] = "OPEN"  # even a stale OPEN must not be trusted on this host
    assert pipeline.needs_eyeball(o)


def test_unverified_posting_needs_an_eyeball(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row()], TODAY)  # UNVERIFIED by default
    assert pipeline.needs_eyeball(led["opportunities"][0])
    led["opportunities"][0]["posting_live"] = "OPEN"
    assert not pipeline.needs_eyeball(led["opportunities"][0])


def test_confirmed_open_outranks_unverified_at_equal_score(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [
            _row(company="Unconfirmed", role="Backend TL", job_id="1", score=9),
            _row(company="Confirmed", role="Backend TL", job_id="2", score=9),
        ],
        TODAY,
    )
    next(o for o in led["opportunities"] if o["company"] == "Confirmed")["posting_live"] = "OPEN"
    assert [o["company"] for o in pipeline.bucket(led)["act_now"]] == ["Confirmed", "Unconfirmed"]


def test_higher_score_still_beats_verification(tmp_path):
    """Verification is a tiebreak, not an override — an unverified 9/10 may well be live."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [
            _row(company="HighUnverified", role="Backend TL", job_id="1", score=9),
            _row(company="LowConfirmed", role="Backend TL", job_id="2", score=7),
        ],
        TODAY,
    )
    next(o for o in led["opportunities"] if o["company"] == "LowConfirmed")["posting_live"] = "OPEN"
    assert [o["company"] for o in pipeline.bucket(led)["act_now"]] == ["HighUnverified", "LowConfirmed"]


def test_board_never_shows_a_bare_unverified_as_actionable(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company="Amdocs", role="AI Evangelist Lead")], TODAY)
    led["opportunities"][0]["url"] = "https://jobs.amdocs.com/careers/job/1"
    md = pipeline.render(led, TODAY)
    assert "OPEN THE LINK" in md
    # Check the TABLE ROW itself, not the legend (which legitimately explains the ✅ marker).
    row = next(line for line in md.splitlines() if line.startswith("|") and "Amdocs" in line)
    assert "✅ OPEN" not in row, "an unverified row must not render as confirmed-open"


def test_upsert_carries_the_polls_liveness_reading(tmp_path):
    """Pass A already checks every URL; discarding it made even fresh Greenhouse rows read
    'unconfirmed', which trains you to ignore the marker entirely."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row() | {"liveness": "OPEN"}], TODAY)
    assert led["opportunities"][0]["posting_live"] == "OPEN"
    assert not pipeline.needs_eyeball(led["opportunities"][0])


def test_upsert_ignores_a_nonconclusive_liveness_value(tmp_path):
    """'skipped' (from --no-liveness) is not a reading and must not overwrite a real one."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row() | {"liveness": "OPEN"}], TODAY)
    pipeline.upsert(led, [_row() | {"liveness": "skipped"}], TODAY)
    assert led["opportunities"][0]["posting_live"] == "OPEN", "a skipped check must not clobber a real one"


def test_upsert_liveness_does_not_leak_into_tool_fields(tmp_path):
    """posting_live must not be in _TOOL_FIELDS - a poll without liveness would blank the
    sweep's reading."""
    assert "posting_live" not in pipeline._TOOL_FIELDS
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row() | {"liveness": "OPEN"}], TODAY)
    pipeline.upsert(led, [_row()], TODAY)  # no liveness key at all
    assert led["opportunities"][0]["posting_live"] == "OPEN"


def test_cli_add_records_manual_provenance_not_a_fake_scan(tmp_path):
    """Regression: `add` inherited upsert's ats_scan default, stamping hand-added rows with
    provenance they never had — which defeats the point of tracking where a role came from."""
    led = tmp_path / "l.json"
    pipeline.main(
        [
            "add",
            "--ledger",
            str(led),
            "--company",
            "SomeCo",
            "--role",
            "Backend TL",
            "--url",
            "https://x.test/1",
            "--today",
            TODAY,
        ]
    )
    ch = pipeline.load(led)["opportunities"][0]["channels"]
    assert [c["channel"] for c in ch] == ["manual"]


def test_cli_add_accepts_a_head_hunter_route_in_one_command(tmp_path):
    led = tmp_path / "l.json"
    assert (
        pipeline.main(
            [
                "add",
                "--ledger",
                str(led),
                "--company",
                "Axon",
                "--role",
                "EM",
                "--url",
                "u",
                "--channel",
                "head_hunter",
                "--name",
                "Noa",
                "--today",
                TODAY,
            ]
        )
        == 0
    )
    ch = pipeline.load(led)["opportunities"][0]["channels"]
    assert [(c["channel"], c["name"]) for c in ch] == [("head_hunter", "Noa")]


def test_cli_add_rejects_an_unnamed_attributed_route(tmp_path):
    led = tmp_path / "l.json"
    assert (
        pipeline.main(
            [
                "add",
                "--ledger",
                str(led),
                "--company",
                "X",
                "--role",
                "Y",
                "--url",
                "u",
                "--channel",
                "head_hunter",
                "--today",
                TODAY,
            ]
        )
        == 1
    )


def test_cli_upsert_from_file_still_records_a_scan(tmp_path):
    """The shortlist path SHOULD claim ats_scan — that is genuinely how those rows arrived."""
    src = tmp_path / "s.json"
    src.write_text(json.dumps([_row()]), encoding="utf-8")
    led = tmp_path / "l.json"
    pipeline.main(["upsert", "--from", str(src), "--ledger", str(led), "--today", TODAY])
    assert [c["channel"] for c in pipeline.load(led)["opportunities"][0]["channels"]] == ["ats_scan"]


# --- route label is shared with the board UI ---------------------------------


def test_render_uses_route_label_so_the_markdown_and_ui_cannot_drift(tmp_path):
    """The ACT NOW Route column must be exactly pipeline.route_label(), not a private copy."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Sett", "Engineering TL", job_id="s1")], TODAY)
    pipeline.add_channel(
        led, opp_id="sett:engineering-tl", channel="head_hunter", name="Chen", today=TODAY, chosen=True
    )
    led["opportunities"][0]["posting_live"] = "OPEN"

    label = pipeline.route_label(led["opportunities"][0])

    assert label == "head_hunter/Chen"
    assert f"| {label} |" in pipeline.render(led, TODAY)


def test_route_label_flags_an_unresolved_multi_route(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Grip", "SW TL", job_id="g1")], TODAY)
    pipeline.add_channel(led, opp_id="grip:sw-tl", channel="head_hunter", name="Noa", today=TODAY)

    assert "multi-route" in pipeline.route_label(led["opportunities"][0])


def test_route_label_of_a_scan_only_row_is_scan(tmp_path):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(job_id="z1")], TODAY)

    assert pipeline.route_label(led["opportunities"][0]) == "scan"


# --- human liveness verdicts -------------------------------------------------


def _one(tmp_path, company="Buildots", role="Future Lead", job_id="b1"):
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row(company, role, job_id=job_id)], TODAY)
    return led


def test_set_liveness_records_the_status_and_who_checked_it(tmp_path):
    led = _one(tmp_path)

    pipeline.set_liveness(led, "buildots:future-lead", "CLOSED", note="404 on the apply page", today=TODAY)

    opp = led["opportunities"][0]
    assert opp["posting_live"] == "CLOSED"
    assert opp["posting_checked"] == {
        "status": "CLOSED",
        "date": TODAY,
        "by": "human",
        "note": "404 on the apply page",
    }


def test_set_liveness_writes_no_history_so_sibling_rows_stay_out_of_watch(tmp_path):
    """bucket() sends a row to WATCH on ANY sibling history. Checking a link is not an outcome."""
    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(led, [_row("Aidoc", "Backend TL", job_id="d1"), _row("Aidoc", "ML TL", job_id="d2")], TODAY)

    pipeline.set_liveness(led, "aidoc:backend-tl", "CLOSED", today=TODAY)

    assert led["opportunities"][0]["history"] == []
    # The checked row goes to WATCH on its own dead posting — that is correct. Its sibling must
    # not follow it there, which is what a history entry would have caused.
    watched = [o["id"] for o in pipeline.bucket(led)["watch"]]
    assert watched == ["aidoc:backend-tl"]
    assert "aidoc:ml-tl" in [o["id"] for o in pipeline.bucket(led)["act_now"]]


def test_set_liveness_never_touches_the_pursuit_decision(tmp_path):
    led = _one(tmp_path)
    pipeline.set_stage(led, "buildots:future-lead", stage="APPLIED", today=TODAY)

    pipeline.set_liveness(led, "buildots:future-lead", "CLOSED", today=TODAY)

    opp = led["opportunities"][0]
    assert opp["stage"] == "APPLIED"
    assert not opp["outcome"]
    assert pipeline.bucket(led)["in_flight"] == [opp]


def test_set_liveness_rejects_a_status_outside_the_vocabulary(tmp_path):
    led = _one(tmp_path)

    with pytest.raises(ValueError, match="DEAD"):
        pipeline.set_liveness(led, "buildots:future-lead", "DEAD", today=TODAY)


def test_set_liveness_rejects_an_unknown_id(tmp_path):
    led = _one(tmp_path)

    with pytest.raises(KeyError):
        pipeline.set_liveness(led, "nope:nope", "OPEN", today=TODAY)


def test_set_liveness_leaves_the_row_prunable_only_if_it_was_already(tmp_path):
    """A link check is not a decision, so it must not make an untouched row immune to prune."""
    led = _one(tmp_path)

    pipeline.set_liveness(led, "buildots:future-lead", "OPEN", today=TODAY)

    assert pipeline._is_untouched(led["opportunities"][0])


def test_validate_rejects_a_malformed_posting_checked(tmp_path):
    led = _one(tmp_path)
    led["opportunities"][0]["posting_checked"] = {"status": "DEAD", "date": TODAY, "by": "human"}

    assert any("posting_checked" in p for p in pipeline.validate(led))


def test_validate_accepts_a_well_formed_posting_checked(tmp_path):
    led = _one(tmp_path)
    pipeline.set_liveness(led, "buildots:future-lead", "OPEN", today=TODAY)

    assert pipeline.validate(led) == []


def test_liveness_cli_round_trips(tmp_path):
    p = tmp_path / "l.json"
    pipeline.save(_one(tmp_path), p)

    rc = pipeline.main(
        [
            "liveness",
            "buildots:future-lead",
            "--status",
            "CLOSED",
            "--note",
            "gone",
            "--ledger",
            str(p),
            "--today",
            TODAY,
        ]
    )

    assert rc == 0
    opp = pipeline.load(p)["opportunities"][0]
    assert opp["posting_live"] == "CLOSED"
    assert opp["posting_checked"]["note"] == "gone"
