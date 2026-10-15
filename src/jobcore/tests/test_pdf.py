"""PDF render test via the real invocation path (subprocess `python -m src.jobcore.pdf`)
— matches how the skill calls it and avoids pytest's in-process third-party import quirk.
"""

import subprocess
import sys
from pathlib import Path

import pytest


def test_render_module_produces_pdf(tmp_path):
    md = tmp_path / "CV_TAILORED.md"
    md.write_text(
        "# Jane Smith\n\n**Engineering Leader**\n\n## SUMMARY\n\nBackend leader.\n\n## EXPERIENCE\n\n- Built things.\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.pdf"
    proc = subprocess.run(
        [sys.executable, "-m", "src.jobcore.pdf", str(md), str(out)],
        cwd=Path(__file__).resolve().parents[3],  # repo root
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 and "No module named" in (proc.stderr or ""):
        pytest.skip(f"PDF deps unavailable in this env: {proc.stderr.strip().splitlines()[-1]}")
    assert proc.returncode == 0, proc.stderr
    # render writes either a .pdf (xhtml2pdf present) or a .html fallback
    produced = out if out.exists() else out.with_suffix(".html")
    assert produced.exists(), f"no output produced; stdout={proc.stdout} stderr={proc.stderr}"
    if produced.suffix == ".pdf":
        from src.jobcore.pdf import page_count

        assert page_count(produced) == 1


def test_auto_derived_name_includes_candidate_name(tmp_path, monkeypatch):
    """Auto-derived (no explicit out) filename must be self-identifying:
    CV_<Candidate_Name>_<Company>.pdf — regression guard for the dropped-name drift.
    Name comes from the CV's H1, NOT env (YOUR_NAME is unset here, as it is in prod)."""
    from src.jobcore.pdf import render

    monkeypatch.delenv("YOUR_NAME", raising=False)
    pos = tmp_path / "positions" / "acme-ai-platform-tl"
    pos.mkdir(parents=True)
    md = pos / "CV_TAILORED.md"
    md.write_text("# Jane Smith\n\n## SUMMARY\n\nBackend leader.\n", encoding="utf-8")

    out = render(md)  # no explicit out_path -> auto-derive

    assert out.stem == "CV_Jane_Smith_Acme_Ai_Platform_Tl", out.stem


def test_name_falls_back_to_env_when_no_h1(tmp_path, monkeypatch):
    """If the CV has no H1, fall back to the YOUR_NAME env var."""
    from src.jobcore.pdf import render

    monkeypatch.setenv("YOUR_NAME", "Jane Smith")
    pos = tmp_path / "positions" / "acme"
    pos.mkdir(parents=True)
    md = pos / "CV_TAILORED.md"
    md.write_text("## SUMMARY\n\nBackend leader.\n", encoding="utf-8")

    out = render(md)

    assert out.stem == "CV_Jane_Smith_Acme", out.stem


def test_cover_letter_name_derived_like_cv(tmp_path, monkeypatch):
    """A COVER_LETTER.md must render to Cover_Letter_<Name>_<Company>.pdf, not
    CV_COVER_LETTER.pdf — the generic 'startswith CV' fallback used to catch this
    stem too, mislabeling a cover letter as a CV. Cover letters have no H1, so the
    candidate name comes from YOUR_NAME (matches the no-H1 CV fallback path)."""
    from src.jobcore.pdf import render

    monkeypatch.setenv("YOUR_NAME", "Jane Smith")
    pos = tmp_path / "positions" / "acme-ai-platform-tl"
    pos.mkdir(parents=True)
    md = pos / "COVER_LETTER.md"
    md.write_text("Dear Hiring Team,\n\nI'm excited to apply.\n\nHappy to connect — Jane\n", encoding="utf-8")

    out = render(md)

    assert out.stem == "Cover_Letter_Jane_Smith_Acme_Ai_Platform_Tl", out.stem


def test_master_cv_name_unchanged(tmp_path):
    """A non-tailored CV file (e.g. master cv.md) keeps its own stem."""
    from src.jobcore.pdf import render

    md = tmp_path / "cv.md"
    md.write_text("# Jane Smith\n\n## SUMMARY\n\nBackend leader.\n", encoding="utf-8")

    out = render(md)

    assert out.stem == "cv", out.stem


def test_master_cv_renders_to_at_most_two_pages(tmp_path):
    """Regression guard: the real master CV must stay within its 2-page budget.

    The synthetic stubs above (a few lines of placeholder text) trivially render to
    1 page and can't catch this — a small wording change to the real cv.md pushed a
    single orphaned line onto a 3rd page (2026-07-22), and nothing caught it because
    no test exercised the actual file's length. This test renders the real content
    on every CI run so a future edit that tips it over fails immediately.
    """
    from src.jobcore.pdf import page_count, render

    repo_root = Path(__file__).resolve().parents[3]
    real_cv = repo_root / "job-tracking" / "cv.md"
    if not real_cv.exists():
        pytest.skip("job-tracking/cv.md not present in this checkout")

    out = render(real_cv, tmp_path / "cv.pdf")
    if out.suffix != ".pdf":
        pytest.skip("PDF deps unavailable in this env")

    assert page_count(out) <= 2, f"master CV rendered to {page_count(out)} pages, budget is 2"
