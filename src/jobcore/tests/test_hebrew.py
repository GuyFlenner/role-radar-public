"""Hebrew / RTL support: keyword matching with attached prefixes, RTL detection, PDF rendering."""

from __future__ import annotations

import pytest

from src.jobcore import pdf
from src.jobcore.profile import _compile_alternation

HEBREW_CV = "# דנה כהן\n\n**מפתחת בכירה**\n\nניסיון של 8 שנים ב-Python וב-AWS, הובלת צוות של 5 מפתחים.\n"


def test_hebrew_keyword_matches_with_attached_prefix_letters():
    pat = _compile_alternation(["מפתח", "מנהל צוות"])
    assert pat is not None
    for text in ("מפתח backend", "דרוש למפתח בכיר", "המפתח הראשי", "דרוש מנהל צוות"):
        assert pat.search(text), text


def test_hebrew_keyword_does_not_match_inside_a_longer_word():
    pat = _compile_alternation(["מפתח"])
    assert pat is not None
    assert not pat.search("מפתחות")  # "keys", a different word: must not be a false positive


def test_latin_keywords_are_unchanged():
    pat = _compile_alternation(["python"])
    assert pat is not None
    assert pat.search("Senior Python dev") and not pat.search("pythonic")


def test_rtl_detection():
    assert pdf.is_rtl(HEBREW_CV)
    assert not pdf.is_rtl("# Jane Smith\n\nSenior engineer, 8 years of Python.")
    assert not pdf.is_rtl("# Jane Smith\n\nLed a team in תל אביב for years, built Python services.")
    assert not pdf.is_rtl("")


def test_html_for_hebrew_is_rtl_and_latin_is_not():
    assert 'dir="rtl"' in pdf.md_to_html(HEBREW_CV)
    assert "dir=" not in pdf.md_to_html("# Jane Smith\n\nEngineer")


def test_hebrew_pdf_renders_when_a_browser_is_available(tmp_path):
    if pdf._find_chrome() is None:
        pytest.skip("SKIPPED[hebrew-pdf]: no Chrome/Chromium/Edge here — Hebrew PDF NOT verified")
    md = tmp_path / "cv.md"
    md.write_text(HEBREW_CV, encoding="utf-8")
    out = pdf.render(md, tmp_path / "cv.pdf")
    assert out.suffix == ".pdf" and out.stat().st_size > 500
    assert pdf.page_count(out) == 1
