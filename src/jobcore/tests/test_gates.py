"""Honesty-gate behaviour when the profile is only partially configured.

The rest of the gate suite is exercised from `test_check.py` through the CLI. This file
covers the seam that suite cannot see: sub-checks that quietly return "no violations"
because they could not run at all.
"""


class TestPartialFactsAreSkippedNotClean:
    """A gate that cannot run must say so. The `has_facts` guard only caught the all-or-nothing case.

    With a `facts` block present but incomplete, `_credential_violations` and
    `_employer_attribution_violations` each returned `[]` — indistinguishable from "checked and
    clean". These are the two checks standing between an inflated rank / a misattributed
    marquee technology and a real recruiter, so a silent no-op is the expensive failure.
    """

    @staticmethod
    def _profile(facts: dict):
        from ..profile import Profile, _skeleton

        raw = _skeleton()
        raw["identity"]["name"] = "Test Person"
        raw["skills"]["keywords"] = ["python"]
        raw["facts"] = facts
        return Profile(raw=raw, path=None)

    def test_credential_on_cv_with_no_configured_credential_is_skipped(self) -> None:
        from ..gates import _credential_violations

        prof = self._profile({"employers": [{"name": "Acme", "markers": ["python"]}]})
        out = _credential_violations("Served as a Major in the reserves.", prof)
        assert any("SKIPPED[credential]" in v for v in out), out

    def test_no_credential_mentioned_stays_quiet(self) -> None:
        """Don't cry skip about a check that had nothing to check."""
        from ..gates import _credential_violations

        prof = self._profile({"employers": [{"name": "Acme", "markers": ["python"]}]})
        assert _credential_violations("Backend engineer. Built services.", prof) == []

    def test_single_employer_cross_attribution_is_skipped(self) -> None:
        from ..gates import _employer_attribution_violations

        prof = self._profile({"employers": [{"name": "Acme", "markers": ["kubernetes"]}]})
        out = _employer_attribution_violations("Acme — ran Kubernetes at scale.", prof)
        assert any("SKIPPED[employer-attribution]" in v for v in out), out

    def test_two_employers_still_actually_check(self) -> None:
        """The skip must not have replaced the real check."""
        from ..gates import _employer_attribution_violations

        prof = self._profile(
            {
                "employers": [
                    {"name": "Acme", "markers": ["ecs fargate"]},
                    {"name": "Globex", "markers": ["kubernetes"]},
                ]
            }
        )
        out = _employer_attribution_violations("Acme — ran Kubernetes at scale.", prof)
        assert out and not any("SKIPPED" in v for v in out), out

    def test_a_profile_with_no_facts_reports_the_outer_skip_only(self) -> None:
        """Unconfigured profiles must not emit three overlapping skip lines."""
        from ..gates import _credential_violations, _employer_attribution_violations

        prof = self._profile({})
        assert _credential_violations("Major, IDF.", prof) == []
        assert _employer_attribution_violations("Acme — Kubernetes.", prof) == []
