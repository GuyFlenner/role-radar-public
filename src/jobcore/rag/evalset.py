"""The RAG eval gate — and the rule that it may never skip silently.

Context. `src.jobcore.check` once printed ``cov=n/a`` when a position had no
committed ``gate_terms.txt``: the ATS-coverage gate was **skipped**, while a
session recorded it as "ATS 100%". First real measurement came in at 84%. The
lesson (memory: feedback_skipped_gate_is_not_green) is that a gate which reports
success while doing nothing is worse than no gate at all.

This module reintroduces the same hazard by design, because the RAG dependencies
are an optional extra: on a bare clone ``chromadb`` is absent and the gate cannot
run. So the contract is explicit and asymmetric:

* **locally** — SKIP, but print a visible ``SKIPPED`` marker, never a pass;
* **in CI** (``CI`` truthy) — a missing extra is an **error**, not a skip.

There is no configuration in which this gate is both absent and silent.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from .goldens import EvalReport, Golden, profile_goldens, score_golden
from .store import Store


class RagExtraMissing(RuntimeError):
    """CI tried to run the RAG gate without the ``rag`` extra installed."""


@dataclass(frozen=True)
class GateResult:
    status: str  # "PASS" | "FAIL" | "SKIPPED"
    detail: str

    @property
    def ok(self) -> bool:
        """SKIPPED is not OK-with-a-shrug; callers must treat it distinctly."""
        return self.status == "PASS"


def _extra_available() -> bool:
    try:
        # Optional extra: absent on a bare clone by design, hence the ignore.
        import chromadb  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def _in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() not in ("", "0", "false", "no")


def evaluate(store: Store, *, collection: str, goldens: Sequence[Golden] | None = None) -> EvalReport:
    """Run every golden against ``store`` and score it deterministically.

    Pure with respect to the backend: pass an InMemoryBackend-backed Store and this
    runs with no dependencies at all, which is how the gate's own behaviour is
    tested. No LLM is involved in any pass/fail decision.
    """
    selected = list(profile_goldens() if goldens is None else goldens)
    report = EvalReport()
    for golden in selected:
        hits = store.query(golden.query, collection=collection, k=golden.k)
        report.outcomes.append(score_golden(golden, hits))
    return report


def run_gate(
    store: Store | None = None,
    *,
    collection: str = "corpus",
    goldens: Sequence[Golden] | None = None,
) -> GateResult:
    """Run the RAG eval gate and turn the report into a gate verdict.

    Raises:
        RagExtraMissing: in CI, when the ``rag`` extra is not installed and no
            ``store`` was supplied. CI must never record a green run for a gate
            that did not execute — that is the ``cov=n/a`` failure this module
            exists to avoid.
    """
    if store is None:
        if not _extra_available():
            if _in_ci():
                raise RagExtraMissing(
                    "the RAG eval gate cannot run: the 'rag' extra is not installed. "
                    "CI must install it (`uv sync --extra rag`) — a skipped gate is not a passed gate."
                )
            return GateResult(status="SKIPPED", detail="rag extra not installed (local run)")
        return GateResult(status="SKIPPED", detail="no corpus index built yet — run `rag ingest` first")

    selected = list(goldens) if goldens is not None else list(profile_goldens())
    if not selected:
        # Vacuously "passing" zero goldens would be the cov=n/a mistake in a new costume.
        return GateResult(
            status="SKIPPED",
            detail="no rag.goldens configured in the profile - retrieval quality was NOT checked",
        )

    report = evaluate(store, collection=collection, goldens=selected)
    status = "PASS" if report.passed else "FAIL"
    return GateResult(status=status, detail=report.summary())
