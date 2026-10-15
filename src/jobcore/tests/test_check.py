"""Tests for the consolidated content-gate runner in jobcore.check."""

from __future__ import annotations

import pytest

from src.jobcore.check import (
    PositionResult,
    check_position,
    company_from_jd,
    format_line,
    main,
    read_terms,
)

CLEAN_CV = """# Jane Smith

**Backend Team Leader | Python**

## PROFESSIONAL SUMMARY

Hands-on backend leader building Python services on AWS with Docker.

## PROFESSIONAL EXPERIENCE

### ExampleStream | Engineering Manager | 2021 - 2024

- Led event-driven Python microservices on Kafka and Kubernetes at scale.
"""

MASTER_CV = CLEAN_CV


def _make_position(tmp_path, slug="acme", cv=CLEAN_CV, jd=None, terms=None, cover=None):
    folder = tmp_path / "positions" / slug
    folder.mkdir(parents=True)
    (folder / "CV_TAILORED.md").write_text(cv, encoding="utf-8")
    if jd is not None:
        (folder / "JD_DESCRIPTION.md").write_text(jd, encoding="utf-8")
    if terms is not None:
        (folder / "gate_terms.txt").write_text(terms, encoding="utf-8")
    if cover is not None:
        (folder / "COVER_LETTER.md").write_text(cover, encoding="utf-8")
    return folder


class TestCompanyFromJD:
    @pytest.mark.parametrize(
        "heading,expected",
        [
            ("# Cyabra — Backend Team Leader", "Cyabra"),
            ("# Sympera AI — Engineering Team Lead", "Sympera AI"),
            ("# Varonis – Backend Team Leader (R&D)", "Varonis"),
            ("# LinearB - Engineering Team Lead", "LinearB"),
            ("# JustACompany", "JustACompany"),
        ],
    )
    def test_extracts_company(self, heading, expected):
        assert company_from_jd(heading + "\n\nbody") == expected

    def test_no_heading_returns_none(self):
        assert company_from_jd("no heading here") is None


class TestReadTerms:
    def test_skips_comments_and_blanks(self, tmp_path):
        (tmp_path / "gate_terms.txt").write_text(
            "# a comment\n\npython\n  aws  \n\n# another\ndocker\n", encoding="utf-8"
        )
        assert read_terms(tmp_path) == ["python", "aws", "docker"]

    def test_missing_file_is_empty(self, tmp_path):
        assert read_terms(tmp_path) == []


class TestCheckPosition:
    def test_clean_position_passes(self, tmp_path, monkeypatch):
        folder = _make_position(tmp_path, jd="# Acme — Backend Lead\n", terms="python\naws\n")
        monkeypatch.chdir(tmp_path)
        (tmp_path / "master.md").write_text(MASTER_CV, encoding="utf-8")
        res = check_position(folder, master_cv=str(tmp_path / "master.md"))
        assert res.passed, res.failures
        assert res.coverage_pct == 100
        assert res.company == "Acme"

    def test_missing_cv_is_an_error_not_a_crash(self, tmp_path):
        folder = tmp_path / "positions" / "empty"
        folder.mkdir(parents=True)
        res = check_position(folder)
        assert not res.passed
        assert "no CV_TAILORED.md" in res.errors

    def test_missing_term_fails_coverage(self, tmp_path):
        folder = _make_position(tmp_path, terms="python\nmongodb\n")
        res = check_position(folder, master_cv=str(tmp_path / "nope.md"))
        assert not res.passed
        assert res.coverage_pct is not None and res.coverage_pct < 100
        assert "mongodb" in res.missing_terms

    def test_no_terms_file_skips_coverage_without_failing(self, tmp_path):
        folder = _make_position(tmp_path)
        res = check_position(folder, master_cv=str(tmp_path / "nope.md"))
        assert res.coverage_pct is None
        assert res.passed

    def test_company_pitch_in_cv_fails(self, tmp_path):
        bad = CLEAN_CV + "\nA direct match for Acme's push toward AI.\n"
        folder = _make_position(tmp_path, cv=bad, jd="# Acme — Backend Lead\n")
        res = check_position(folder, master_cv=str(tmp_path / "nope.md"))
        assert not res.passed
        assert res.pitch_hits

    def test_repeated_clause_is_reported_as_drift(self, tmp_path):
        echo = "led event driven python microservices on kafka and kubernetes at scale"
        cv = CLEAN_CV + f"\n- {echo}\n\n## MORE\n\n- {echo}\n"
        folder = _make_position(tmp_path, cv=cv)
        res = check_position(folder, master_cv=str(tmp_path / "nope.md"))
        assert not res.passed
        assert res.repeated_clauses

    def test_overqualification_never_blocks(self):
        """Advisory must not appear in failures — it is the candidate's call."""
        res = PositionResult(slug="x", overqualified="OVER-QUALIFICATION (advisory)")
        assert res.passed
        assert res.failures == []

    def test_too_many_pages_fails(self):
        res = PositionResult(slug="x", pages=3)
        assert not res.passed
        assert any("3 pages" in f for f in res.failures)

    def test_two_pages_passes(self):
        assert PositionResult(slug="x", pages=2).passed


class TestReportAndExit:
    def test_line_is_single_line_when_passing(self):
        line = format_line(PositionResult(slug="acme", coverage_pct=100, pages=2))
        assert "\n" not in line
        assert line.startswith("PASS")

    def test_failing_line_explains_why(self):
        res = PositionResult(slug="acme", coverage_pct=90, missing_terms=["mongodb"])
        line = format_line(res)
        assert line.startswith("FAIL")
        assert "mongodb" in line

    def test_exit_code_zero_on_pass(self, tmp_path, capsys):
        folder = _make_position(tmp_path, terms="python\n")
        rc = main([str(folder), "--master-cv", str(tmp_path / "nope.md")])
        assert rc == 0
        assert "1/1 passed" in capsys.readouterr().out

    def test_exit_code_one_on_failure(self, tmp_path):
        folder = _make_position(tmp_path, terms="python\nmongodb\n")
        assert main([str(folder), "--master-cv", str(tmp_path / "nope.md")]) == 1

    def test_bad_usage_returns_two(self, tmp_path):
        assert main([str(tmp_path / "does-not-exist")]) == 2

    def test_json_mode_is_parseable(self, tmp_path, capsys):
        import json

        folder = _make_position(tmp_path, terms="python\n")
        main([str(folder), "--json", "--master-cv", str(tmp_path / "nope.md")])
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["slug"] == "acme"
        assert payload[0]["passed"] is True

    def test_multiple_positions_in_one_call(self, tmp_path, capsys):
        """The whole point: N positions, one invocation."""
        a = _make_position(tmp_path, slug="a", terms="python\n")
        b = _make_position(tmp_path, slug="b", terms="python\n")
        rc = main([str(a), str(b), "--master-cv", str(tmp_path / "nope.md")])
        assert rc == 0
        assert "2/2 passed" in capsys.readouterr().out
