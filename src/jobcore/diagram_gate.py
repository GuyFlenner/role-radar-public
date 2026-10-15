"""Lint every tracked .drawio in the repo. The gate wrapper around `drawio_lint`.

`drawio_lint.py` is 900 lines with 54 tests and was wired into NOTHING — no verify_all.sh step,
no CI step, no pre-push hook. A diagram could ship with arrows through boxes and text invisible on
a dark canvas and every gate would still say green. That is the same "dormant checker" defect this
repo has now found four times: the capability existed, the enforcement did not.

Zero-diagram handling is the part worth reading. Reporting PASS over zero files is exactly the
`goldens 0/0` defect, so this exits:

    files found   verdict     exit
    -------------------------------
    0             SKIPPED        0   nothing claimed - a repo may legitimately have no diagrams
    >=1, clean    PASS           0
    >=1, errors   FAIL           1

SKIPPED exits 0 on purpose: making a diagram-less repo permanently red is how a gate gets deleted
by whoever is unblocking the build. But it prints SKIPPED, never PASS, so the summary line cannot
be mistaken for verification that did not happen.

CLI::

    uv run python -m src.jobcore.diagram_gate
    uv run python -m src.jobcore.diagram_gate --root docs/architecture
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .console import force_utf8_output
from .drawio_lint import lint_file

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def tracked_diagrams(root: Path | None = None) -> list[Path]:
    """Every .drawio tracked by git, so an experiment in a scratch dir cannot fail the build.

    Untracked files are deliberately out of scope: a work-in-progress diagram should not be able
    to break a teammate's push.
    """
    base = root or REPO_ROOT
    out = subprocess.run(["git", "ls-files", "*.drawio"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    files = [REPO_ROOT / line.strip() for line in out.stdout.splitlines() if line.strip()]
    return sorted(f for f in files if f.exists() and base in (f, *f.parents))


def run(root: Path | None = None) -> tuple[str, list[str], int]:
    """Returns (status, messages, exit_code)."""
    files = tracked_diagrams(root)
    if not files:
        return ("SKIPPED", ["no tracked .drawio files - diagram UX was NOT checked"], 0)

    problems: list[str] = []
    for f in files:
        errors, warns = lint_file(f)
        rel = f.relative_to(REPO_ROOT).as_posix()
        problems += [f"{rel}: ERROR {e}" for e in errors]
        if warns:
            problems += [f"{rel}: warn  {w}" for w in warns]

    errs = [p for p in problems if " ERROR " in p]
    status = "FAIL" if errs else "PASS"
    return (status, problems, 1 if errs else 0)


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(description="Lint every tracked .drawio for layout/contrast defects.")
    ap.add_argument("--root", default=None, help="only lint diagrams under this directory")
    args = ap.parse_args(argv)

    status, messages, code = run(Path(args.root) if args.root else None)
    n = len(tracked_diagrams(Path(args.root) if args.root else None))
    errs = sum(1 for m in messages if " ERROR " in m)
    warns = sum(1 for m in messages if " warn  " in m)

    if status == "SKIPPED":
        print(f"SKIPPED  drawio  {messages[0]}")
        return code
    print(f"{status}  drawio  {n} diagram(s), {errs} error(s), {warns} warning(s)")
    for m in messages:
        print(f"  - {m}")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
