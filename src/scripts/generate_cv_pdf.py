"""Thin shim → src.jobcore.pdf (the canonical renderer). Kept so existing docs
and any lingering references to this path keep working. One implementation.

Usage: uv run python src/scripts/generate_cv_pdf.py <CV.md> [out.pdf]
"""

import sys
from pathlib import Path

from src.jobcore.pdf import render

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/scripts/generate_cv_pdf.py <CV.md> [out.pdf]")
        sys.exit(1)
    render(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
