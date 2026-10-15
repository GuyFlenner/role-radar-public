"""Tests for `console.force_utf8_output`.

This helper was *extracted* from `check.py` during the platform refactor, and the three
encoding tests that covered it were dropped in the move — so a function two CLIs depend on
shipped untested. The cp1252 crash it prevents has now recurred three times in this codebase;
it is exactly the kind of thing that must have a test attached to the shared implementation
rather than to one of its callers.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from ..console import force_utf8_output


class TestForceUtf8Output:
    def test_reconfigures_a_cp1252_stream_to_utf8(self, monkeypatch) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        monkeypatch.setattr(sys, "stdout", stream)
        force_utf8_output()
        assert sys.stdout.encoding.lower().replace("-", "") == "utf8"

    def test_a_reconfigured_stream_can_write_the_gate_glyphs(self, monkeypatch) -> None:
        """The actual regression: PASS/FAIL glyphs crashed the gate on a cp1252 console."""
        raw = io.BytesIO()
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
        force_utf8_output()
        sys.stdout.write("─── ✅ ❌ ⚠️  ₪ ───")
        sys.stdout.flush()
        assert "✅" in raw.getvalue().decode("utf-8")

    def test_is_idempotent_and_safe_on_an_already_utf8_stream(self, monkeypatch) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        monkeypatch.setattr(sys, "stdout", stream)
        force_utf8_output()
        force_utf8_output()
        assert sys.stdout.encoding.lower().replace("-", "") == "utf8"

    def test_survives_a_stream_that_cannot_be_reconfigured(self, monkeypatch) -> None:
        """Captured/replaced stdout has no `reconfigure`; the helper must not take down the CLI."""

        class Dumb:
            encoding = "cp1252"

            def write(self, s: str) -> int:
                return len(s)

        monkeypatch.setattr(sys, "stdout", Dumb())
        force_utf8_output()  # must not raise

    def test_end_to_end_under_a_cp1252_console(self) -> None:
        """The real shape of the bug: a child process whose stdout genuinely is cp1252."""
        code = (
            "import sys;"
            "sys.path.insert(0, '.');"
            "from src.jobcore.console import force_utf8_output;"
            "force_utf8_output();"
            "print('✅ ₪ ─')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            env={"PYTHONIOENCODING": "cp1252", "PATH": ""},
            cwd=".",
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


class TestEveryCliGuardsItsEncoding:
    """Meta-test: a new CLI cannot ship without the guard.

    This bug has been fixed four separate times, each time at one call site, and each fix left
    the other CLIs broken. At the time this test was written 12 of 14 entry points were
    unguarded — including `pipeline render`, which prints the pipeline board.

    Severity is not cosmetic. ASCII-only output merely garbles ('·' -> '?', '→' -> a literal
    \u2192), but any non-Latin content raises UnicodeEncodeError outright on a cp1252 console.
    Since this platform is explicitly geography-configurable, non-Latin job titles are the
    NORMAL case for a non-English-market user — the exact thing role-radar exists to support.
    `job-radar/SKILL.md` even documented a manual `PYTHONIOENCODING=utf-8` workaround for it.
    """

    @staticmethod
    def _cli_modules() -> list[Path]:
        from ..csv_ingest import REPO_ROOT

        src = REPO_ROOT / "src" / "jobcore"
        out = []
        for path in sorted(src.rglob("*.py")):
            if "tests" in path.parts or path.name in ("__init__.py", "console.py"):
                continue
            text = path.read_text(encoding="utf-8")
            if '__name__ == "__main__"' in text or "\ndef main(" in text:
                out.append(path)
        return out

    def test_there_are_cli_modules_to_check(self) -> None:
        """Guard the guard: a broken discovery expression would make this suite vacuous."""
        assert len(self._cli_modules()) >= 10, self._cli_modules()

    def test_every_cli_module_calls_force_utf8_output(self) -> None:
        missing = [p.name for p in self._cli_modules() if "force_utf8_output()" not in p.read_text(encoding="utf-8")]
        assert missing == [], (
            f"these CLIs print without the encoding guard and will raise on non-Latin output: {missing}. "
            f"Add `from .console import force_utf8_output` and call it first in main()."
        )

    def test_non_latin_output_raises_without_the_guard(self) -> None:
        """Proves the failure the guard prevents is real, not theoretical."""
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with pytest.raises(UnicodeEncodeError):
            stream.write("מפתח תוכנה — Team Lead")
            stream.flush()

    def test_non_latin_output_survives_with_the_guard(self, monkeypatch) -> None:
        raw = io.BytesIO()
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict"))
        force_utf8_output()
        print("מפתח תוכנה — Team Lead")
        sys.stdout.flush()
        assert "מפתח" in raw.getvalue().decode("utf-8")
