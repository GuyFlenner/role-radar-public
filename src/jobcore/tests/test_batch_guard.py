"""batch_guard tests — offline, no env leakage (monkeypatch only), no model calls.

Why this module exists: "never run a job-seeking batch with AUTO_APPROVE=1" was documented in
14 files and enforced in **zero lines of code** (audited 2026-08-05: 31 prose occurrences, not one
in an executable path). A rule that only lives in prose is the same anti-pattern as a regex that
can never fire — see feedback_a_green_control_that_means_nothing. These tests are the control.
"""

import json

import pytest

from src.jobcore import batch_guard


def _shortlist(*companies):
    """Shortlist-shaped rows: a top-level LIST of dicts, as ats_shortlist/shortlist emit."""
    return [{"company": c, "title": "Backend Team Lead", "match_score": 8} for c in companies]


# --- reading the env flag ----------------------------------------------------


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "Y", "on"])
def test_auto_approve_recognised_from_truthy_values(monkeypatch, raw):
    monkeypatch.setenv("AUTO_APPROVE", raw)
    assert batch_guard.auto_approve_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "", "off", "maybe"])
def test_auto_approve_off_for_everything_else(monkeypatch, raw):
    monkeypatch.setenv("AUTO_APPROVE", raw)
    assert batch_guard.auto_approve_enabled() is False


def test_auto_approve_absent_is_off(monkeypatch):
    monkeypatch.delenv("AUTO_APPROVE", raising=False)
    assert batch_guard.auto_approve_enabled() is False


# --- counting companies, not rows -------------------------------------------


def test_companies_are_counted_distinctly():
    """Three reqs at one company is ONE fan-out target, not three. Cyera had two 8/10 reqs on the
    2026-08-05 board, so this is the normal case, not an edge case."""
    rows = _shortlist("Cyera", "Cyera", "Gong")
    assert batch_guard.companies_in(rows) == ["Cyera", "Gong"]


def test_companies_in_tolerates_blank_and_duplicate_casing():
    rows = _shortlist("Gong", "gong", "", "  Cato  ")
    assert batch_guard.companies_in(rows) == ["Cato", "Gong"]


def test_companies_in_accepts_an_object_wrapper():
    """shortlist JSON is a top-level list today, but accept {"positions": [...]} so a future
    wrapper cannot silently make the guard count zero companies and pass everything."""
    rows = {"positions": _shortlist("Gong", "Cyera")}
    assert batch_guard.companies_in(rows) == ["Cyera", "Gong"]


# --- the actual refusal ------------------------------------------------------


def test_attended_run_is_allowed_at_any_size():
    """The cap is about UNATTENDED fan-out. With a human clearing gates, 30 companies is slow,
    not dangerous — so the guard must not become a general batch-size limit."""
    v = batch_guard.check_batch(_shortlist(*[f"Co{i}" for i in range(30)]), auto_approve=False)
    assert v.allowed is True
    assert v.exit_code == 0


def test_hands_off_run_within_the_cap_is_allowed():
    v = batch_guard.check_batch(_shortlist("A", "B", "C"), auto_approve=True, cap=5)
    assert v.allowed is True
    assert v.exit_code == 0


def test_hands_off_run_over_the_cap_is_REFUSED():
    v = batch_guard.check_batch(_shortlist(*[f"Co{i}" for i in range(30)]), auto_approve=True, cap=5)
    assert v.allowed is False
    assert v.exit_code == 1
    assert v.company_count == 30
    assert "30" in v.reason and "5" in v.reason


def test_hands_off_run_exactly_at_the_cap_is_allowed():
    """Boundary: the cap is a maximum, not a strict upper bound. 5 companies with cap=5 runs."""
    v = batch_guard.check_batch(_shortlist("A", "B", "C", "D", "E"), auto_approve=True, cap=5)
    assert v.allowed is True


def test_cap_can_be_raised_by_env_but_still_applies(monkeypatch):
    monkeypatch.setenv("MAX_HANDS_OFF_COMPANIES", "8")
    rows = _shortlist(*[f"Co{i}" for i in range(8)])
    assert batch_guard.check_batch(rows, auto_approve=True).allowed is True
    rows9 = _shortlist(*[f"Co{i}" for i in range(9)])
    assert batch_guard.check_batch(rows9, auto_approve=True).allowed is False


def test_a_nonsense_cap_falls_back_to_the_default_instead_of_disabling_the_guard(monkeypatch):
    """A guard that silently turns itself off on bad config is worse than no guard."""
    for bad in ("0", "-3", "abc", ""):
        monkeypatch.setenv("MAX_HANDS_OFF_COMPANIES", bad)
        v = batch_guard.check_batch(_shortlist(*[f"Co{i}" for i in range(30)]), auto_approve=True)
        assert v.allowed is False, bad
        assert v.cap == batch_guard.DEFAULT_MAX_HANDS_OFF_COMPANIES, bad


# --- never-skippable gates (the job-apply spec's requirement) ----------------
# docs/superpowers/specs/2026-08-05-job-apply-orchestrator-design.md requires a gate that
# AUTO_APPROVE cannot bypass, and warns against duplicating that logic across skills. One
# registry here, so both job-seeking and job-apply ask the same question.


def test_a_never_skippable_gate_still_requires_a_human_under_auto_approve():
    for gate in batch_guard.NEVER_SKIPPABLE:
        assert batch_guard.gate_requires_human(gate, auto_approve=True) is True, gate


def test_submit_and_outreach_are_never_skippable():
    """The irreversible ones: anything that puts a CV or a message in front of a real person."""
    assert "submit" in batch_guard.NEVER_SKIPPABLE
    assert batch_guard.gate_requires_human("submit", auto_approve=True) is True


def test_lead_approval_is_never_skippable():
    """email-lead-triage's score+explanation yes/no, before anything is handed to job-apply."""
    assert "lead_approval" in batch_guard.NEVER_SKIPPABLE
    assert batch_guard.gate_requires_human("lead_approval", auto_approve=True) is True


def test_an_ordinary_gate_IS_skippable_under_auto_approve():
    """The control. If every gate were never-skippable, AUTO_APPROVE would be meaningless and the
    registry would be indistinguishable from a blanket refusal."""
    assert batch_guard.gate_requires_human("shortlist", auto_approve=True) is False
    assert batch_guard.gate_requires_human("shortlist", auto_approve=False) is True


def test_an_unknown_gate_name_defaults_to_requiring_a_human():
    """Fail closed. A typo'd or newly-added gate name must not silently become skippable."""
    assert batch_guard.gate_requires_human("some-new-gate", auto_approve=True) is True


# --- CLI ---------------------------------------------------------------------


def test_cli_returns_0_when_the_batch_is_allowed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AUTO_APPROVE", "1")
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_shortlist("A", "B")), encoding="utf-8")
    assert batch_guard.main(["--shortlist", str(p)]) == 0
    assert "OK" in capsys.readouterr().out


def test_cli_returns_1_and_explains_when_it_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AUTO_APPROVE", "1")
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_shortlist(*[f"Co{i}" for i in range(12)])), encoding="utf-8")
    assert batch_guard.main(["--shortlist", str(p)]) == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "12" in out


def test_cli_returns_2_on_a_missing_file(tmp_path, capsys):
    assert batch_guard.main(["--shortlist", str(tmp_path / "nope.json")]) == 2


def test_cli_auto_approve_flag_overrides_a_clean_env(tmp_path, monkeypatch, capsys):
    """So the guard is testable and callable without mutating the caller's environment."""
    monkeypatch.delenv("AUTO_APPROVE", raising=False)
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_shortlist(*[f"Co{i}" for i in range(12)])), encoding="utf-8")
    assert batch_guard.main(["--shortlist", str(p)]) == 0, "attended run is fine at 12"
    assert batch_guard.main(["--shortlist", str(p), "--auto-approve"]) == 1
