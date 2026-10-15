"""Golden questions and the property assertions that score them.

Modelled on the one eval gate in this estate that genuinely blocks CI —
a production code-graph service's `eval/golden_questions.toml` + `eval/baselines.json`. The
properties here are **deterministic**: no LLM decides pass or fail. An LLM-as-judge
layer can sit alongside later (`judge.py`) but must never be what the gate blocks
on, because a judged gate is an alerting signal, not a merge gate.

Assertion vocabulary, deliberately small:

* ``must_retrieve`` — this source MUST appear in the top-k. Catches "we stopped
  finding the right document".
* ``must_not_retrieve`` — this source must NOT appear in the top-k. Catches
  "a stale or irrelevant document started ranking".
* ``top_source`` — this source must rank first. The strictest form.
* ``min_score`` — the best hit must clear this floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .store import Hit


@dataclass(frozen=True)
class Golden:
    """One golden question plus its deterministic expectations."""

    id: str
    query: str
    must_retrieve: tuple[str, ...] = ()
    must_not_retrieve: tuple[str, ...] = ()
    top_source: str | None = None
    min_score: float = 0.0
    k: int = 5

    @property
    def asserts_something(self) -> bool:
        """False when this golden would pass no matter what retrieval returned.

        `score_golden` collects failures from four optional assertions; with none of them set it
        returns zero failures and the golden "passes". Three such goldens produced
        `PASS goldens 3/3` over a corpus the gate never actually tested — the same `all([])`
        defect as an empty golden LIST, one level further down, and it survived a clean-clone
        check because a vacuous pass looks exactly like a real one.
        """
        return bool(self.must_retrieve or self.must_not_retrieve or self.top_source or self.min_score > 0.0)


@dataclass(frozen=True)
class GoldenOutcome:
    id: str
    passed: bool
    failures: tuple[str, ...]
    retrieved: tuple[str, ...]


@dataclass
class EvalReport:
    outcomes: list[GoldenOutcome] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(outcome.passed for outcome in self.outcomes)

    @property
    def failed_ids(self) -> list[str]:
        return [outcome.id for outcome in self.outcomes if not outcome.passed]

    def summary(self) -> str:
        total = len(self.outcomes)
        ok = total - len(self.failed_ids)
        line = f"goldens {ok}/{total} passed"
        if self.failed_ids:
            line += f" — FAILED: {', '.join(self.failed_ids)}"
        return line


def score_golden(golden: Golden, hits: list[Hit]) -> GoldenOutcome:
    """Apply every assertion on ``golden`` to ``hits``. Pure and deterministic."""
    sources = [hit.source for hit in hits]
    failures: list[str] = []

    if not golden.asserts_something:
        return GoldenOutcome(
            id=golden.id,
            passed=False,
            failures=(
                "no assertions: set at least one of must_retrieve / must_not_retrieve / "
                "top_source / min_score, or this golden passes unconditionally",
            ),
            retrieved=tuple(sources),
        )

    for required in golden.must_retrieve:
        if required not in sources:
            failures.append(f"must_retrieve: {required!r} absent from top-{golden.k} ({sources})")

    for forbidden in golden.must_not_retrieve:
        if forbidden in sources:
            failures.append(f"must_not_retrieve: {forbidden!r} present in top-{golden.k}")

    if golden.top_source is not None:
        actual_top = sources[0] if sources else None
        if actual_top != golden.top_source:
            failures.append(f"top_source: expected {golden.top_source!r}, got {actual_top!r}")

    if golden.min_score > 0.0:
        best = hits[0].score if hits else 0.0
        if best < golden.min_score:
            failures.append(f"min_score: best {best:.3f} < {golden.min_score:.3f}")

    return GoldenOutcome(
        id=golden.id,
        passed=not failures,
        failures=tuple(failures),
        retrieved=tuple(sources),
    )


#: Every key a profile golden may set. Anything else is a typo or an invented key, and silently
#: ignoring it yields a golden that asserts nothing while looking strict.
_GOLDEN_KEYS = frozenset({"id", "query", "must_retrieve", "must_not_retrieve", "top_source", "min_score", "k"})


def profile_goldens() -> tuple[Golden, ...]:
    """Golden questions from ``rag.goldens`` in the candidate profile.

    Goldens assert *which document* answers a question, so they are inseparable from the corpus
    — and the corpus lives in ``PROFILE_DIR``, not here. Hardcoding them in shared code is how
    this gate arrived asserting one person's private filenames and failing 0/3 for everyone else.

    Empty tuple means "not configured", which :func:`evalset.run_gate` reports as SKIPPED rather
    than passing vacuously.
    """
    from ..profile import load

    out: list[Golden] = []
    for g in load().raw.get("rag", {}).get("goldens", []):
        if not isinstance(g, dict) or not g.get("query"):
            continue
        # An unrecognised key is silently dropped by the constructor below, which turns a golden
        # the author believed was strict into one that asserts nothing. That is exactly how
        # `expect_source` — a key that has never existed in this schema — got written into a CI
        # fixture and produced a green gate over three unasserted questions. Fail loudly instead.
        unknown = set(g) - _GOLDEN_KEYS
        if unknown:
            raise ValueError(
                f"rag.goldens[{g.get('id', g['query'][:24])!r}]: unknown key(s) {sorted(unknown)}. "
                f"Valid keys: {sorted(_GOLDEN_KEYS)}"
            )
        out.append(
            Golden(
                id=str(g.get("id", g["query"][:24])),
                query=str(g["query"]),
                must_retrieve=tuple(g.get("must_retrieve", ())),
                must_not_retrieve=tuple(g.get("must_not_retrieve", ())),
                top_source=g.get("top_source"),
                min_score=float(g.get("min_score", 0.0)),
                k=int(g.get("k", 5)),
            )
        )
    return tuple(out)


# Kept EMPTY on purpose. The previous value asserted three documents from the original author's
# private notes directory, so a fresh clone scored 0/3 and the gate looked broken rather than
# unconfigured. Corpus-specific goldens belong in the profile — see :func:`profile_goldens`.
DEFAULT_GOLDENS: tuple[Golden, ...] = ()

_EXAMPLE_GOLDENS_DO_NOT_USE: tuple[Golden, ...] = (
    # G1 was originally written asserting AI_ECOSYSTEM_GAP_AUDIT_2026-08-01.md. Its
    # first run against the real corpus failed and was RIGHT to: the canonical
    # freshness document is RAG_REINGEST_FRESHNESS_PLAIN_ENGLISH.md, which the
    # retrieval ranked correctly. The golden was wrong, not the code — which is the
    # useful direction for a new gate to fail in.
    Golden(
        id="G1-freshness-doc",
        query="upsert delete by id freshness invalidation stale chunks",
        must_retrieve=("RAG_REINGEST_FRESHNESS_PLAIN_ENGLISH.md",),
        min_score=0.1,
    ),
    Golden(
        id="G2-a2a-adr",
        query="hub and spoke fan out versus A2A agent swarms decision",
        must_retrieve=("ADR-hub-and-spoke-vs-a2a.md",),
        min_score=0.1,
    ),
    Golden(
        id="G3-accuracy-card",
        query="spoken claim traps say instead of overclaim",
        must_retrieve=("ACCURACY_CARD.md",),
        min_score=0.1,
    ),
)
