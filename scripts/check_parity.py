#!/usr/bin/env python3
"""ONE-OFF migration tool, not a gate — read this before wiring it into CI.

It takes a path to the SOURCE repo, which CI does not have and never will, so it cannot run
there. An audit flagged it as "a dormant checker wired into nothing"; the honest resolution is
that it was never a gate, not that it needs bolting into one. A checker that cannot run in CI
and is documented as a gate is worse than one documented as a tool.

Run it by hand after pulling infra changes across from the source repo:

    uv run python scripts/check_parity.py /path/to/source-repo

The ONGOING equivalents — the checks that do run on every push — are
`src/jobcore/skill_lint.py` (skill prose vs the real config surface) and the meta-tests in
`test_console.py` / `test_profile.py`.

Compare this platform's infra against an upstream source repo and report what is missing.

WHY THIS EXISTS
---------------
This repo was extracted from a personal job-search repo by copying files. Two things were
silently dropped and only found by accident, days apart:

  * ``.mcp.json`` — the Exa MCP server config, so the job-radar `deep` tier was dead.
  * ``src/jobcore/rag/`` — an entire 9-module package plus 6 test files, including
    ``test_ci_gate_not_skippable.py``.

Neither failed a test, because the tests came across too. The extraction had **no post-copy
manifest check** — the copy loop's own success was treated as proof the copy was complete.
This script is that missing check, so the next sync is verified rather than assumed.

WHAT IT DOES NOT DO
-------------------
It never copies anything. Deciding whether a missing file is a gap or a deliberate exclusion
is a judgement call — personal data (``positions/``, ``job-tracking/``, a real CV) is *supposed*
to be absent. Everything it prints is grouped so that distinction stays visible.

    uv run python scripts/check_parity.py ../source-repo
    uv run python scripts/check_parity.py ../source-repo --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.jobcore.console import force_utf8_output  # noqa: E402

#: Paths that must NEVER be copied here. Their absence is the design, not a gap.
#: Keep in sync with .gitignore and the reasoning in SETUP.md.
INTENTIONALLY_ABSENT: tuple[str, ...] = (
    "positions/",
    "job-tracking/",
    "mentoring/",
    "_archive/",
    "architecture-workflows/",  # personal study corpus; RAG reads it from PROFILE_DIR instead
    "day-of-interview/",
    "interview-questions/",
    "sidekiq-to-modern-pipeline/",
    ".claude-global-backup/",  # employer MCP config + personal email
    ".claude/skills/_legacy/",
    "STUDY_INDEX.md",
    "AGENTS_README.md",
    "docs/superpowers/",  # source-repo design history; this repo keeps only its own
)

#: Files whose ABSENCE is a real gap even though they hold secrets upstream — we need the
#: key-free example instead, and forgetting it silently disables a whole feature (the Exa case).
NEEDS_EXAMPLE: dict[str, str] = {
    ".mcp.json": ".mcp.json.example",
    ".env": ".env.example",
    "ats_targets.json": "ats_targets.example.json",
}


def tracked_files(repo: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def is_intentional(path: str) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in INTENTIONALLY_ABSENT)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="Path to the upstream repo to compare against")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args(argv)
    force_utf8_output()

    here = Path(__file__).resolve().parent.parent
    source = Path(args.source).expanduser().resolve()
    if not (source / ".git").exists():
        print(f"parity: {source} is not a git repo", file=sys.stderr)
        return 2

    gaps: list[str] = []
    excluded: list[str] = []
    for f in tracked_files(source):
        if (here / f).exists():
            continue
        (excluded if is_intentional(f) else gaps).append(f)

    # A secret-bearing upstream file is fine to omit, but its example MUST exist here.
    missing_examples = [
        f"{real} → {example} (feature is silently disabled without it)"
        for real, example in NEEDS_EXAMPLE.items()
        if (source / real).exists() and not (here / example).exists()
    ]

    if args.json:
        print(json.dumps({"gaps": gaps, "missing_examples": missing_examples, "excluded": len(excluded)}, indent=2))
        return 1 if gaps or missing_examples else 0

    print(f"parity vs {source}\n")
    if missing_examples:
        print(f"MISSING EXAMPLE CONFIG ({len(missing_examples)}) — a whole feature is off:")
        for m in missing_examples:
            print(f"  ✗ {m}")
        print()
    if gaps:
        print(f"UNEXPLAINED GAPS ({len(gaps)}) — review each; copy it or add it to INTENTIONALLY_ABSENT:")
        for g in gaps:
            print(f"  ? {g}")
        print()
    print(f"intentionally absent (personal data / source-repo history): {len(excluded)} files")
    if not gaps and not missing_examples:
        print("\n✓ parity clean — every upstream infra file is present or explicitly excluded")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
