"""Console output helpers shared by every CLI in this package.

This exists because the same defect was fixed twice, badly. ``check.py`` prefixes failures with
"→" and can print non-Latin company names; ``rag/cli.py`` prints chunk text containing arrows and
em-dashes. Windows consoles default to cp1252, so the *first* such line raised
``UnicodeEncodeError`` and killed the run **before** printing why the gate failed — which reads
like a broken tool and invites skipping the gate.

The fix was applied to ``check.py`` and not to ``rag/cli.py``, so the RAG CLI still crashed on
Windows. Putting it here means a CLI added later inherits it instead of rediscovering the bug.
"""

from __future__ import annotations

import sys


def force_utf8_output() -> None:
    """Make CLI output survive a legacy Windows console.

    Encode as UTF-8 and never raise on an unmappable character. Best-effort by design: a stream
    that cannot be reconfigured (``io.StringIO``, some pytest capture shims) is left alone rather
    than crashing the caller.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
