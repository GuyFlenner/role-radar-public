"""Small filesystem helpers for the pipeline (CV read/write, position folders).

Moved from the retired src/agents/tools/job_tools.py so nothing depends on the
old LangGraph package. Deterministic, no LLM.
"""

from pathlib import Path

from .csv_ingest import JOB_TRACKING, REPO_ROOT


def read_cv_md() -> str:
    """Read the master CV markdown file (job-tracking/cv.md)."""
    cv_path = JOB_TRACKING / "cv.md"
    return cv_path.read_text(encoding="utf-8") if cv_path.exists() else ""


def write_enhanced_cv(content: str) -> Path:
    """Write the ATS-enhanced CV to job-tracking/cv_enhanced.md."""
    out = JOB_TRACKING / "cv_enhanced.md"
    out.write_text(content, encoding="utf-8")
    return out


def ensure_position_folder(company_slug: str) -> Path:
    """Create positions/{slug}/ if it doesn't exist and return the path."""
    folder = REPO_ROOT / "positions" / company_slug
    folder.mkdir(parents=True, exist_ok=True)
    return folder
