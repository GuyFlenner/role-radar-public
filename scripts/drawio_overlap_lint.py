#!/usr/bin/env python3
"""Thin CLI shim -> src.jobcore.drawio_lint (the single source of truth).

The lint logic moved into the importable jobcore core so it is unit-testable and
covered by the CI contract; this path is kept because the skills and the drawio
retro reference it. See src/jobcore/drawio_lint.py.

Usage: uv run python scripts/drawio_overlap_lint.py <file.drawio> [--min-gap 20] [--hub-degree 4]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.jobcore.drawio_lint import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
