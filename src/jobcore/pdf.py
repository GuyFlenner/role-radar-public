"""CV markdown → 2-page PDF. Canonical renderer (moved from src/scripts/).

`render(md_path, out_path)` is the public entry; `page_count(pdf)` for tests/checks.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_RTL_CHARS = re.compile(r"[\u0590-\u05FF\u0600-\u06FF]")  # Hebrew + Arabic blocks
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)

RTL_CSS = """
html { direction: rtl; }
body { direction: rtl; text-align: right;
  font-family: "Arial", "Arial Hebrew", "Noto Sans Hebrew", "Noto Sans Arabic", "DejaVu Sans", sans-serif; }
ul, ol { padding-right: 18px; padding-left: 0; }
"""


def is_rtl(md_content: str, threshold: float = 0.3) -> bool:
    """True when the document is mostly Hebrew/Arabic letters (so it needs RTL layout)."""
    letters = len(_LETTERS.findall(md_content))
    return bool(letters) and len(_RTL_CHARS.findall(md_content)) / letters >= threshold


def _find_chrome() -> str | None:
    """A Chromium-family browser that can print HTML to PDF with correct bidi shaping."""
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    ):
        if os.path.exists(path):
            return path
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and os.path.exists(os.path.join(env, "chromium")):
        return os.path.join(env, "chromium")
    return None


def _render_with_chrome(html: str, out_path: Path) -> bool:
    chrome = _find_chrome()
    if not chrome:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "cv.html"
        src.write_text(html, encoding="utf-8")
        cmd = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out_path}",
            src.as_uri(),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=90)
        except (subprocess.SubprocessError, OSError):
            return False
    return out_path.exists() and out_path.stat().st_size > 0


CV_CSS = """
@page { margin: 12mm 14mm; size: A4; }
h2 { page-break-after: avoid; -pdf-keep-with-next: true; }
h3 { page-break-after: avoid; -pdf-keep-with-next: true; }
ul { page-break-inside: avoid; }
body {
  font-family: Arial, sans-serif;
  font-size: 10pt;
  line-height: 1.3;
  color: #1a1a1a;
  margin: 28px auto;
  max-width: 800px;
}
h1 {
  font-size: 17pt;
  color: #1a2e44;
  border-bottom: 2px solid #1a2e44;
  padding-bottom: 4px;
  margin-bottom: 2px;
}
h1 + p { margin-top: 1px; margin-bottom: 6px; font-size: 9.5pt; color: #333; }
h2 {
  font-size: 11pt;
  color: #1a2e44;
  border-bottom: 1px solid #bbb;
  margin-top: 10px;
  margin-bottom: 4px;
  padding-bottom: 2px;
  text-transform: uppercase;
}
h3 { font-size: 10pt; color: #222; margin-top: 7px; margin-bottom: 2px; }
ul { margin: 2px 0 4px 18px; padding: 0; }
li { margin-bottom: 2px; }
strong { color: #111; }
hr { border: none; border-top: 1px solid #ddd; margin: 6px 0; }
a { color: #1a5276; text-decoration: none; }
p { margin: 2px 0 5px; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #ddd; padding: 3px 6px; text-align: left; }
th { background: #eaf0f6; }
"""


def _strip_metadata(md_content: str) -> str:
    lines = md_content.splitlines()
    lines = [line for line in lines if not line.strip().startswith("*Tailored for:")]
    while lines and lines[-1].strip() in ("", "---"):
        lines.pop()
    return "\n".join(lines)


def md_to_html(md_content: str, rtl: bool | None = None) -> str:
    import markdown as md_lib

    body = md_lib.markdown(md_content, extensions=["tables", "fenced_code"])
    if rtl is None:
        rtl = is_rtl(md_content)
    attrs = ' dir="rtl" lang="he"' if rtl else ""
    extra = RTL_CSS if rtl else ""
    return f"""<!DOCTYPE html>
<html{attrs}><head>
<meta charset="utf-8">
<title>CV</title>
<style>{CV_CSS}{extra}</style>
</head><body>
{body}
</body></html>"""


def _candidate_name(md_content: str) -> str:
    """Extract the candidate's name from the CV's first `# ` H1, as an underscored
    slug. This is the authoritative source — it always matches the rendered CV and
    needs no config. Falls back to the `YOUR_NAME` env var, then to empty.
    """
    for line in md_content.splitlines():
        s = line.strip()
        if s.startswith("# "):
            name = s[2:].strip()
            for sep in (" — ", " – ", " - ", " | ", ","):  # cut a trailing tagline
                name = name.split(sep, 1)[0].strip()
            if name:
                return "_".join(name.split())
            break
    return "_".join(os.environ.get("YOUR_NAME", "").split())


def _derive_stem(md_path: Path, md_content: str) -> str:
    """Auto-derive the output filename stem from a CV markdown path.

    The standard tailored file `positions/<slug>/CV_TAILORED.md` becomes
    `CV_<Candidate_Name>_<Company>.pdf` — self-identifying so the name never drifts
    out (it used to render as `CV_<slug>.pdf`, dropping the candidate entirely).
    Any other already-named `CV*.md` (e.g. the master `cv.md`) keeps its own stem.
    """
    stem = md_path.stem
    if stem.upper() == "CV_TAILORED":
        name = _candidate_name(md_content)
        company = "_".join(w.capitalize() for w in md_path.parent.name.split("-"))
        return "_".join(p for p in ("CV", name, company) if p)
    if stem.upper() == "COVER_LETTER":
        name = _candidate_name(md_content)
        company = "_".join(w.capitalize() for w in md_path.parent.name.split("-"))
        return "_".join(p for p in ("Cover_Letter", name, company) if p)
    if stem.upper().startswith("CV"):
        return stem
    return f"CV_{stem}"


def render(md_path: Path, out_path: Path | None = None) -> Path:
    """Render a CV markdown file to PDF (xhtml2pdf); falls back to HTML if unavailable."""
    md_path = Path(md_path)
    md_content = _strip_metadata(md_path.read_text(encoding="utf-8"))
    html = md_to_html(md_content)

    if out_path is None:
        out_path = md_path.parent / f"{_derive_stem(md_path, md_content)}.pdf"
    out_path = Path(out_path)

    if is_rtl(md_content):
        # xhtml2pdf has no bidi shaping and no Hebrew glyphs in its built-in fonts: it prints boxes.
        # A Chromium-family browser (already on most machines) does this correctly.
        if _render_with_chrome(html, out_path):
            print(f"PDF saved: {out_path}")
            return out_path
        print(
            "WARNING: Hebrew/Arabic CV but no Chrome/Chromium/Edge found - cannot render a correct PDF. "
            "Install Chrome, or export to .docx (docx_export) and print from Word."
        )

    try:
        from xhtml2pdf import pisa  # type: ignore[import-untyped]  # ships no py.typed

        with open(out_path, "wb") as pdf_file:
            result = pisa.CreatePDF(html, dest=pdf_file, encoding="utf-8")
        if not result.err:
            print(f"PDF saved: {out_path}")
            return out_path
        print(f"xhtml2pdf error: {result.err}")
    except ImportError:
        pass

    html_path = out_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    print(f"HTML saved: {html_path} (install xhtml2pdf for PDF)")
    return html_path


def page_count(pdf_path: Path) -> int:
    # pypdf is the current package; PyPDF2 is its pre-3.0 name. Accepting either keeps this
    # working on an older install. The names are the same class, hence the no-redef ignore.
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - only on a pre-3.0 install
        from PyPDF2 import PdfReader  # type: ignore[import-not-found,no-redef]
    return len(PdfReader(str(pdf_path)).pages)


# Backwards-compatible alias for the old script entry point.
generate = render


if __name__ == "__main__":
    import sys

    from .console import force_utf8_output

    force_utf8_output()  # a candidate name can be non-Latin; printing the path must not raise

    if len(sys.argv) < 2:
        print("Usage: python -m src.jobcore.pdf <CV.md> [out.pdf]")
        sys.exit(1)
    render(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
