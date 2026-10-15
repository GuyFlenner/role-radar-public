"""The private-scope firewall: declared terms/dates must never reach candidate-facing text."""

import json

from src.jobcore import check
from src.jobcore import profile as profile_mod
from src.jobcore.gates import find_private_scope_leak


def _prof(tmp_path, privacy):
    raw = profile_mod._skeleton()
    raw["identity"]["name"] = "Test Person"
    raw["skills"]["keywords"] = ["python"]
    raw["privacy"] = privacy
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return profile_mod.load(p)


def test_nothing_configured_means_no_opinion_not_a_hit(tmp_path):
    prof = _prof(tmp_path, {"never_mention": [], "private_dates": []})
    assert find_private_scope_leak("any text at all, including a lawsuit", prof) == []
    assert prof.has_privacy_rules is False
    assert "none configured" in prof.brief()


def test_declared_terms_match_on_word_boundaries_case_insensitively(tmp_path):
    prof = _prof(tmp_path, {"never_mention": ["lawsuit", "Dana Levi"], "private_dates": []})
    hits = find_private_scope_leak("Left after a LAWSUIT. Manager: dana levi.", prof)
    assert len(hits) == 2
    # a term is a WORD, not a substring: "lawsuitless" must not trip "lawsuit".
    assert find_private_scope_leak("a lawsuitless summary", prof) == []


def test_dates_are_caught_in_every_form_a_person_types(tmp_path):
    prof = _prof(tmp_path, {"never_mention": [], "private_dates": ["2026-03-14"]})
    for form in ("2026-03-14", "14.3.2026", "14/03/2026", "March 14", "14 March", "march 14th", "14th March"):
        assert find_private_scope_leak(f"Ended on {form}.", prof), form
    assert find_private_scope_leak("Ended 2026-03-15.", prof) == []


def test_hits_are_deduplicated(tmp_path):
    prof = _prof(tmp_path, {"never_mention": ["lawsuit"], "private_dates": []})
    assert len(find_private_scope_leak("lawsuit. LAWSUIT. lawsuit.", prof)) == 1


def test_validate_rejects_a_malformed_private_date(tmp_path):
    prof = _prof(tmp_path, {"never_mention": [], "private_dates": ["14/03/2026"]})
    assert any("not an ISO date" in p for p in profile_mod.validate(prof))


def test_the_position_gate_fails_a_cv_that_leaks(tmp_path, monkeypatch):
    prof = _prof(tmp_path, {"never_mention": ["lawsuit"], "private_dates": []})
    monkeypatch.setattr("src.jobcore.gates.default_profile", lambda: prof)
    folder = tmp_path / "acme"
    folder.mkdir()
    (folder / "CV_TAILORED.md").write_text("# Test Person\n\nLeft over a lawsuit.\n", encoding="utf-8")
    res = check.check_position(folder, master_cv=str(tmp_path / "none.md"))
    assert res.private_scope_hits
    assert any(f.startswith("private scope:") for f in res.failures)
