"""The publish guard, and proof it refuses.

Born from a real conflict: `scripts/setup-github.sh` carried its own regex and refused to create
the remote because it could not distinguish `positions/_template/COMPANY_RESEARCH.md` (shipped
scaffolding) from a real candidate's research. `test_profile.py` had an allowlist; the shell did
not. Two implementations of one rule always drift, and the dangerous direction is the copy that
is more LENIENT — a shell regex waving a real CV onto GitHub.
"""

from __future__ import annotations

from ..publish_check import ALLOWLIST, UNSAFE_PATTERNS, is_allowlisted, main, tracked_files, unsafe_tracked


class TestTheRealTreeIsPublishable:
    def test_nothing_unsafe_is_tracked(self) -> None:
        offenders = unsafe_tracked()
        assert offenders == [], "\n".join(offenders)

    def test_there_are_tracked_files_to_check(self) -> None:
        """Guard the guard: an empty file list would make every assertion here vacuous."""
        assert len(tracked_files()) > 50

    def test_cli_exits_zero(self) -> None:
        assert main([]) == 0


class TestItActuallyRefuses:
    """A guard never seen failing is not verified."""

    def test_a_real_position_folder_is_refused(self) -> None:
        assert unsafe_tracked(["positions/acme-corp/CV_TAILORED.md"])

    def test_a_cv_anywhere_is_refused(self) -> None:
        assert unsafe_tracked(["cv.md"])
        assert unsafe_tracked(["some/dir/cv_backend.pdf"])

    def test_a_real_profile_is_refused(self) -> None:
        assert unsafe_tracked(["profile.json"])
        assert unsafe_tracked(["somewhere/profile.json"])

    def test_secrets_are_refused(self) -> None:
        assert unsafe_tracked([".env"])
        assert unsafe_tracked([".mcp.json"])
        assert unsafe_tracked(["ats_targets.json"])

    def test_job_tracking_is_refused(self) -> None:
        assert unsafe_tracked(["job-tracking/PIPELINE_BOARD.md"])

    def test_every_unsafe_pattern_has_a_case_that_trips_it(self) -> None:
        """No pattern may be unreachable — an unreachable rule is decoration."""
        samples = {
            r"(^|/)(positions|job-tracking)/": "positions/x/y.md",
            r"(^|/)cv(_[^/]*)?\.(md|pdf)$": "cv.md",
            r"(^|/)profile\.json$": "profile.json",
            r"(^|/)\.env$": ".env",
            r"(^|/)\.mcp\.json$": ".mcp.json",
            r"(^|/)ats_targets\.json$": "ats_targets.json",
        }
        for pattern, _why in UNSAFE_PATTERNS:
            assert pattern in samples, f"no sample for {pattern!r}"
            assert unsafe_tracked([samples[pattern]]), f"{pattern!r} never fires"


class TestTheAllowlistCannotAdmitRealData:
    def test_scaffolding_passes(self) -> None:
        assert is_allowlisted("positions/_template/STATUS.md")
        assert is_allowlisted("profiles/_template.json")
        assert is_allowlisted("profiles/_fixtures/rag_demo/profile.json")
        assert unsafe_tracked(["positions/_template/STATUS.md"]) == []

    def test_a_real_slug_can_never_match_an_allowlist_entry(self) -> None:
        """Every entry is `_`-prefixed scaffolding or an explicitly named template file."""
        for entry, why in ALLOWLIST:
            leaf = entry.rstrip("/").split("/")[-1]
            assert leaf.startswith("_"), f"allowlist entry {entry!r} is not scaffolding"
            assert why.strip(), f"allowlist entry {entry!r} has no justification"

    def test_the_allowlist_stays_small(self) -> None:
        assert len(ALLOWLIST) <= 5, [e for e, _ in ALLOWLIST]

    def test_a_prefix_lookalike_directory_is_not_admitted(self) -> None:
        """`positions/_template/` must not admit `positions/_templateX/`.

        The allowlist entry ends in `/` precisely so a `startswith` cannot match a longer sibling
        name. Without the trailing slash, anyone could smuggle real data into
        `positions/_template_backup/` and the guard would wave it through.
        """
        assert not is_allowlisted("positions/_templates_real/CV_TAILORED.md")
        assert unsafe_tracked(["positions/_templates_real/CV_TAILORED.md"])

    def test_a_nested_fake_template_under_a_real_slug_is_refused(self) -> None:
        assert not is_allowlisted("positions/acme/_template/CV_TAILORED.md")
        assert unsafe_tracked(["positions/acme/_template/CV_TAILORED.md"])
