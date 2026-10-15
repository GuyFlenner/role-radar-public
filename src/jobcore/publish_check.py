"""Is this working tree safe to publish? One source of truth, used by the script AND the tests.

This exists because there were two answers to that question and they disagreed.

`test_profile.py` maintained a `SCAFFOLDING_ALLOWLIST` so that documentation-only files under
`positions/_template/` and the RAG CI fixture could be tracked. `scripts/setup-github.sh`
carried its own independent regex that knew nothing about it, and refused to create the remote:

    ERROR: personal data or secrets are tracked. Refusing to create a remote.
      positions/_template/COMPANY_RESEARCH.md
      profiles/_fixtures/rag_demo/profile.json

The guard was RIGHT to refuse — it had no way to distinguish a shipped template from a real
candidate's research. But two implementations of one rule always drift, and the failure modes
are asymmetric: this time it blocked a safe push, next time the shell regex is the lenient one
and it waves through a real CV. Both callers now ask this module.

CLI::

    uv run python -m src.jobcore.publish_check          # exit 1 if anything unsafe is tracked
    uv run python -m src.jobcore.publish_check --list    # show the rules and the allowlist
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from .console import force_utf8_output

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Tracked paths matching any of these are personal data or secrets and must never be published.
UNSAFE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(^|/)(positions|job-tracking)/", "per-position work and pipeline state live in $PROFILE_DIR"),
    (r"(^|/)cv(_[^/]*)?\.(md|pdf)$", "a CV is the most personal file in the whole system"),
    (r"(^|/)profile\.json$", "a profile carries name, salary band, employers and never-claim facts"),
    (r"(^|/)\.env$", "secrets"),
    (r"(^|/)\.mcp\.json$", "may embed an API key"),
    (r"(^|/)ats_targets\.json$", "the user's own target-company list"),
)

#: The ONLY exceptions. Documentation and CI fixtures containing no personal data.
#:
#: Every entry must be justified here, and `test_publish_check.py` asserts each one is either
#: under a `_`-prefixed directory or explicitly named — a real company slug or a real person's
#: profile can never match, so this list cannot quietly grow to admit one.
ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("positions/_template/", "the per-position layout template — scaffolding, no data"),
    ("profiles/_template.json", "the profile template a new user copies"),
    ("profiles/_fixtures/", "synthetic fixtures, incl. the RAG CI corpus; marked SYNTHETIC"),
)


def is_allowlisted(path: str) -> bool:
    return any(path == entry or path.startswith(entry) for entry, _ in ALLOWLIST)


def tracked_files(root: Path | None = None) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=root or REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def unsafe_tracked(files: list[str] | None = None) -> list[str]:
    """Tracked paths that must not be published, as ``path — reason`` lines."""
    compiled = [(re.compile(p), why) for p, why in UNSAFE_PATTERNS]
    offenders: list[str] = []
    for path in files if files is not None else tracked_files():
        if is_allowlisted(path):
            continue
        for rx, why in compiled:
            if rx.search(path):
                offenders.append(f"{path} — {why}")
                break
    return offenders


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(description="Refuse to publish a tree containing personal data.")
    ap.add_argument("--list", action="store_true", help="print the rules and the allowlist")
    args = ap.parse_args(argv)

    if args.list:
        print("UNSAFE (never publish):")
        for pattern, why in UNSAFE_PATTERNS:
            print(f"  {pattern:44s} {why}")
        print("\nALLOWLISTED (scaffolding and synthetic fixtures only):")
        for entry, why in ALLOWLIST:
            print(f"  {entry:44s} {why}")
        return 0

    offenders = unsafe_tracked()
    if offenders:
        print(f"FAIL  publish-check  {len(offenders)} unsafe tracked path(s)")
        for o in offenders:
            print(f"  - {o}")
        return 1
    print(f"PASS  publish-check  {len(tracked_files())} tracked files, none carrying personal data")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
