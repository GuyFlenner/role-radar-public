"""profile.json — the structured company snapshot, and the refusal that makes it servable.

``positions/<slug>/`` is full of things that must never reach a browser: salary walk-away floors,
named-interviewer dossiers with handling advice, honest self-assessed gaps, clearance status. The
sidecar exists so the board can show a company snapshot *without* opening that door.

That makes ``validate_profile`` a control, not a formality — so most of this file is written to fail
if the control is removed ([[feedback_a_green_control_that_means_nothing]]). A denylist of key
*names* would be trivially defeated by putting a salary floor in ``what_they_do``, which is why the
value scan is tested as hard as the key scan.
"""

import json
from pathlib import Path

import pytest

from src.jobcore import company_snapshot as profile

VALID = {
    "schema_version": "1.0",
    "company": "Payoneer",
    "industry": "Financial Technology (FinTech) / Cross-Border Payments",
    "founded": 2005,
    "size": "1,871-2,500 employees",
    "funding": "Public (NASDAQ: PAYO)",
    "what_they_do": "Cross-border payment infrastructure for SMBs in 190+ countries.",
    "differentiator": "Local receiving accounts in multiple currencies plus 2,000+ marketplace integrations.",
    "tech_stack": ["Java", "C#", ".NET", "React", "MongoDB", "Apache Spark"],
    "sources": ["https://investor.payoneer.com", "https://www.payoneer.com/about/"],
    "updated": "2026-08-07",
}


def test_a_valid_profile_has_no_problems():
    assert profile.validate_profile(VALID) == []


def test_missing_optional_fields_are_fine():
    minimal = {"schema_version": "1.0", "company": "Payoneer", "updated": "2026-08-07"}

    assert profile.validate_profile(minimal) == []


def test_a_non_dict_is_refused():
    assert profile.validate_profile(["not", "an", "object"])


# --- refusal 1: closed schema ------------------------------------------------


def test_an_unknown_key_is_refused():
    bad = {**VALID, "glassdoor_login": "jane@example.com"}

    problems = profile.validate_profile(bad)
    assert any("unknown field" in p and "glassdoor_login" in p for p in problems)


# --- refusal 2: sensitive key names ------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["salary", "salary_floor", "walk_away", "comp_band", "interviewer", "phone", "email", "clearance", "debrief"],
)
def test_a_sensitive_key_name_is_refused(key):
    bad = {**VALID, key: "anything at all"}

    assert profile.validate_profile(bad), f"{key!r} must not be storable in a servable profile"


# --- refusal 3: sensitive VALUES (the one a key denylist cannot catch) -------


@pytest.mark.parametrize(
    "value",
    [
        "Pays around ₪45,000 a month",
        "Budget is NIS 50-55K for this seat",
        "band is 45-50K, walk away below 32K",
        "Walk away below 32,000",
        "floor ~46-48K per the head-hunter",
        "ILS 58,000 plus RSUs",
        "salary range discussed with Noa",
    ],
)
def test_a_salary_shaped_value_is_refused(value):
    """The realistic leak: a compensation number pasted into a legitimate prose field."""
    bad = {**VALID, "what_they_do": value}

    problems = profile.validate_profile(bad)
    assert problems, f"{value!r} reached a servable field unchallenged"
    assert any("sensitive" in p.lower() for p in problems)


def test_a_sensitive_value_is_caught_inside_a_list_too():
    bad = {**VALID, "tech_stack": ["Java", "pays ₪60K"]}

    assert profile.validate_profile(bad)


# --- regressions from the Phase 5 security review ----------------------------
# Both of these passed validation before the review. They are the realistic leaks, not exotic
# ones: (a) one level of nesting, (b) ordinary ways a person writes a salary band.


@pytest.mark.parametrize(
    "value",
    [
        {"note_inner": "Walk away below 32,000 NIS"},
        {"a": {"b": "walk away below 32,000"}},
        [{"url": "x", "note": "walk away below 32,000"}],
        {"walk_away": "32000"},
    ],
)
def test_a_sensitive_value_nested_in_a_dict_is_refused(value):
    """A scanner that only walks strings and lists is defeated by one level of nesting."""
    bad = {**VALID, "differentiator": value}

    assert profile.validate_profile(bad), f"nested payload reached a servable field: {value!r}"


@pytest.mark.parametrize(
    "value",
    [
        "Comp band 45K-50K depending on level",
        "Range 45,000-50,000 depending on level",
        "Comp band 45 to 50k depending on level",
        "Pays 45k-50 depending on level",
        "compensation range is negotiable",
    ],
)
def test_realistic_salary_band_phrasings_are_refused(value):
    bad = {**VALID, "what_they_do": value}

    assert profile.validate_profile(bad), f"salary band phrasing slipped through: {value!r}"


@pytest.mark.parametrize(
    "value",
    [
        # Single-sided figures — no range at all. The second security pass found these; the first
        # fix only handled ranges.
        "Recruiter mentioned floor is 45k during the call.",
        "We offer a competitive package, up to 60k base plus equity.",
        "base 50k + equity",
        "offered 50k",
        "50k base",
        "ceiling 60k",
        "starting at 45k",
        "floor is around 45,000",
        "monthly 48000",
        # Range phrasings a person actually types.
        "between 45 and 50k",
        "45-50 thousand",
    ],
)
def test_single_sided_and_worded_compensation_is_refused(value):
    """Regression, Phase 5 re-verify.

    `floor is 45k` is a one-word rephrase of the exact leak the module docstring names, and the
    original `\\bfloor\\s*~?\\s*\\d` pattern required the digits to follow "floor" immediately.
    """
    bad = {**VALID, "differentiator": value}

    assert profile.validate_profile(bad), f"compensation slipped through: {value!r}"


@pytest.mark.parametrize(
    "value",
    [
        "Supports up to 100k concurrent users worldwide.",
        "Processes 50k transactions per second.",
        "Serves 100k businesses in 190+ countries.",
        "Up to 250,000 customers on the platform.",
        "Between 1,871 and 2,500 employees globally.",
        "Local receiving accounts in multiple currencies plus 2,000+ marketplace integrations.",
    ],
)
def test_scale_figures_are_not_mistaken_for_compensation(value):
    """The counter-test that keeps the control usable.

    A company legitimately writes "up to 100k concurrent users". If the money-word patterns fired
    on that, authors would stop writing profiles or start disabling the check — and a control
    people route around protects nothing. Every money pattern carries a scale-noun guard for this.
    """
    fine = {**VALID, "what_they_do": value}

    assert profile.validate_profile(fine) == [], f"legitimate scale figure was refused: {value!r}"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("what_they_do", {"nested": "anything"}),
        ("differentiator", ["a", "list"]),
        ("tech_stack", "Java, C#"),
        ("sources", {"a": "b"}),
        ("founded", "2005"),
        ("founded", True),
        ("tech_stack", ["Java", 7]),
    ],
)
def test_a_field_of_the_wrong_shape_is_refused(field, value):
    """Shape enforcement is the primary lock — a declared field holding an object is how a
    nested salary gets past a scanner in the first place."""
    bad = {**VALID, field: value}

    assert profile.validate_profile(bad), f"{field}={value!r} was accepted"


def test_ordinary_prose_with_numbers_is_not_flagged():
    """The control must refuse compensation, not every integer — a filter that fires on
    'founded 2005' or '190+ countries' would be turned off within a week."""
    fine = {
        **VALID,
        "what_they_do": "Serves 190+ countries and processes billions in volume across 2,000+ marketplaces.",
        "size": "1,871-2,500 employees",
    }

    assert profile.validate_profile(fine) == []


# --- loading -----------------------------------------------------------------


def test_load_profile_reads_a_valid_file(tmp_path):
    folder = tmp_path / "payoneer"
    folder.mkdir()
    (folder / "profile.json").write_text(json.dumps(VALID), encoding="utf-8")

    loaded, problems = profile.load_profile(folder)

    assert problems == []
    assert loaded["company"] == "Payoneer"


def test_load_profile_missing_file_returns_none(tmp_path):
    loaded, problems = profile.load_profile(tmp_path)

    assert loaded is None
    assert problems == []


def test_load_profile_refuses_to_return_invalid_content(tmp_path):
    """The whole point: an invalid profile yields None, never the raw dict."""
    folder = tmp_path / "payoneer"
    folder.mkdir()
    (folder / "profile.json").write_text(json.dumps({**VALID, "what_they_do": "pays ₪60K"}), encoding="utf-8")

    loaded, problems = profile.load_profile(folder)

    assert loaded is None
    assert problems


# --- coverage report ---------------------------------------------------------


def _ledger_with_folder(tmp_path, folder):
    from src.jobcore import pipeline

    led = pipeline.load(tmp_path / "l.json")
    pipeline.upsert(
        led,
        [{"company": "Payoneer", "title": "Eng Leader", "job_id": "p1", "url": "u", "match_score": 8, "source": "s"}],
        "2026-08-07",
    )
    pipeline._find(led, opp_id="payoneer:eng-leader")["position_folder"] = str(folder) if folder else None
    return led


def test_survey_reports_a_valid_profile_as_ok(tmp_path):
    folder = tmp_path / "positions" / "payoneer"
    folder.mkdir(parents=True)
    (folder / "profile.json").write_text(json.dumps(VALID), encoding="utf-8")

    rows = profile.survey(_ledger_with_folder(tmp_path, folder))

    assert [r["state"] for r in rows] == ["ok"]


def test_survey_reports_a_folder_with_no_profile_as_missing(tmp_path):
    folder = tmp_path / "positions" / "payoneer"
    folder.mkdir(parents=True)

    rows = profile.survey(_ledger_with_folder(tmp_path, folder))

    assert [r["state"] for r in rows] == ["missing"]


def test_survey_reports_an_unsafe_profile_as_refused(tmp_path):
    """The interesting case: the file exists, so somebody wrote it — and the board is withholding it."""
    folder = tmp_path / "positions" / "payoneer"
    folder.mkdir(parents=True)
    (folder / "profile.json").write_text(json.dumps({**VALID, "what_they_do": "floor is 45k"}), encoding="utf-8")

    rows = profile.survey(_ledger_with_folder(tmp_path, folder))

    assert rows[0]["state"] == "refused"
    assert rows[0]["detail"]


def test_survey_skips_rows_with_no_folder(tmp_path):
    assert profile.survey(_ledger_with_folder(tmp_path, None)) == []


def test_status_exits_zero_when_nothing_is_refused(tmp_path, capsys):
    from src.jobcore import pipeline

    folder = tmp_path / "positions" / "payoneer"
    folder.mkdir(parents=True)
    led_path = tmp_path / "l.json"
    pipeline.save(_ledger_with_folder(tmp_path, folder), led_path)

    assert profile.main(["status", "--ledger", str(led_path)]) == 0
    assert "1 missing" in capsys.readouterr().out


def test_status_exits_one_when_a_profile_is_refused(tmp_path, capsys):
    from src.jobcore import pipeline

    folder = tmp_path / "positions" / "payoneer"
    folder.mkdir(parents=True)
    (folder / "profile.json").write_text(json.dumps({**VALID, "salary": "45k"}), encoding="utf-8")
    led_path = tmp_path / "l.json"
    pipeline.save(_ledger_with_folder(tmp_path, folder), led_path)

    assert profile.main(["status", "--ledger", str(led_path)]) == 1
    assert "REFUSED" in capsys.readouterr().out


def test_status_survives_a_console_that_cannot_encode_its_output(tmp_path):
    """Regression by precedent: the same trap killed `src.jobcore.usage` and the drawio lint.

    Two things this test had to get right, both learned the hard way when CI rejected the first
    version:

    * **The codepage must actually fail.** The first attempt used ``cp1252``, which encodes the
      summary's ``·`` perfectly well (0xB7) — so it proved nothing. ``ascii`` cannot, which is what
      makes this exercise ``errors="replace"`` rather than decorate it.
    * **The parent must not decode as text.** ``text=True`` decodes the child's bytes as UTF-8, so a
      deliberately non-UTF-8 stdout blows up in the *test harness* instead of testing the child.
      Read bytes, decode leniently, assert on that.
    """
    import os
    import subprocess
    import sys

    from src.jobcore import pipeline

    folder = tmp_path / "positions" / "payoneer"
    folder.mkdir(parents=True)
    led_path = tmp_path / "l.json"
    pipeline.save(_ledger_with_folder(tmp_path, folder), led_path)

    proc = subprocess.run(
        [sys.executable, "-m", "src.jobcore.company_snapshot", "status", "--ledger", str(led_path)],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        cwd=Path(__file__).resolve().parents[3],
    )
    stdout = proc.stdout.decode("ascii", errors="replace")
    stderr = proc.stderr.decode("ascii", errors="replace")

    assert proc.returncode == 0, stderr
    assert "UnicodeEncodeError" not in stderr
    assert "profile coverage" in stdout, "the numbers must survive even when a character does not"


def test_load_profile_survives_malformed_json(tmp_path):
    folder = tmp_path / "payoneer"
    folder.mkdir()
    (folder / "profile.json").write_text("{not json", encoding="utf-8")

    loaded, problems = profile.load_profile(folder)

    assert loaded is None
    assert any("parse" in p.lower() or "json" in p.lower() for p in problems)
