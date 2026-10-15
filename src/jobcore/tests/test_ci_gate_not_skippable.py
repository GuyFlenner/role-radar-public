"""A gate that skips silently is worse than no gate.

Direct regression guard for the 2026-07-30 defect: `src.jobcore.check` reported
``cov=n/a`` for positions with no committed ``gate_terms.txt`` — the coverage gate
was skipped, and a session recorded it as "ATS 100%". Real score was 84%.

The RAG gate reintroduces the same hazard because its dependencies are an optional
extra, so the contract is asymmetric and tested: SKIP locally with a visible
marker, ERROR in CI.
"""

from __future__ import annotations

import pytest

from src.jobcore.rag import evalset
from src.jobcore.rag.evalset import RagExtraMissing


class TestGateCannotSkipSilently:
    def test_missing_extra_is_an_error_under_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RED against a naive implementation, which returns SKIPPED and passes CI."""
        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(evalset, "_extra_available", lambda: False)

        with pytest.raises(RagExtraMissing):
            evalset.run_gate()

    def test_missing_extra_skips_visibly_outside_ci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Locally a skip is allowed — but it must say SKIPPED, never PASS."""
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setattr(evalset, "_extra_available", lambda: False)

        result = evalset.run_gate()

        assert result.status == "SKIPPED"
        assert result.ok is False, "a skipped gate must never report as OK"

    @pytest.mark.parametrize("value", ["", "0", "false", "no"])
    def test_falsey_ci_values_are_not_ci(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        """`CI=0` in a local shell must not turn a skip into a hard failure."""
        monkeypatch.setenv("CI", value)
        monkeypatch.setattr(evalset, "_extra_available", lambda: False)

        assert evalset.run_gate().status == "SKIPPED"

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_truthy_ci_values_are_ci(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("CI", value)
        monkeypatch.setattr(evalset, "_extra_available", lambda: False)

        with pytest.raises(RagExtraMissing):
            evalset.run_gate()
