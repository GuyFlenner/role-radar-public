"""CLI for the RAG lane — and the gate entry point `verify_all.sh` calls.

    uv run python -m src.jobcore.rag eval            # blocking gate: exit 1 on regression
    uv run python -m src.jobcore.rag query "<text>"  # ad-hoc retrieval

The gate builds its index **in memory, from the working tree, on every run**. That
is deliberate: no persisted index means no stale index, so the gate can never pass
because it is reading yesterday's corpus. It also keeps the gate dependency-free
and fast enough that nobody is tempted to skip it.

⚠️ PII denylist. The corpus root contains tailored CVs, recruiter correspondence and
the candidate's private/legal material. `EXCLUDED_DIRS` keeps those out of any index,
and `test_rag_cli.py` asserts it — an ingestion path that quietly widened would be a
privacy defect, not a bug. See memory `feedback_private_hearing_scope`.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from ..console import force_utf8_output
from .evalset import GateResult, evaluate
from .goldens import profile_goldens
from .store import Hit, InMemoryBackend, Store, VectorBackend

if TYPE_CHECKING:  # `rerank` pulls sentence-transformers/torch — keep it out of runtime imports.
    from .rerank import Scorer


def default_globs() -> tuple[str, ...]:
    """Where the RAG corpus lives — from the profile, not hardcoded.

    This used to be ``("architecture-workflows/*.md",)``: one person's study directory, which
    does not exist in a clone and is not in this repo. Set ``rag.corpus_globs`` in your profile
    (paths are resolved relative to ``PROFILE_DIR``, so your notes stay outside the shared repo).

    An empty tuple means "no corpus configured" — distinct from "configured but empty", which the
    gate still treats as a FAIL.
    """
    from ..profile import load

    prof = load()
    globs = prof.raw.get("rag", {}).get("corpus_globs", [])
    return tuple(str(g) for g in globs)


#: Back-compat alias for callers that imported the constant. Prefer :func:`default_globs`.
DEFAULT_GLOBS: tuple[str, ...] = ()

RERANK_FETCH_K = 5
"""Over-fetch before reranking. Recall is 10/10 at top-3, so 5 is ample headroom."""

EXCLUDED_DIRS: tuple[str, ...] = (
    "job-tracking/hearing",
    "job-tracking/_drafts",
    "positions",
    "_archive",
)
"""Never ingested. Private, candidate-facing, or third-party correspondence."""

EXCLUDED_FILES: frozenset[str] = frozenset({"CLAIMS.md"})
"""Index/ledger documents — excluded for RETRIEVAL QUALITY, not privacy.

`CLAIMS.md` quotes a fragment of every topic in the estate by design — it is a claim->proof
ledger. That makes it a systematic retrieval polluter: it mentions everything and explains
nothing, so it outranks the document that actually explains a topic.

⚠️ Scoped to `CLAIMS.md` alone, and the gate is why. A first attempt also excluded
`ACCURACY_CARD.md` on the same reasoning and immediately failed `G3-accuracy-card`, which
requires retrieving that card for "spoken claim traps say instead of overclaim". The card is
a legitimate retrieval TARGET — someone genuinely asks it questions — whereas the ledger is
an index of pointers. The distinction is *"is this document ever the right answer?"*, not
*"is it a meta document?"*.

Caught by the blocking gate on 2026-08-03. Adding claim rows that mentioned upsert / stale
chunks / delete-by-filename took `CLAIMS.md` to a perfect 1.0000 on the query
"upsert delete by id freshness invalidation stale chunks", pushing the real freshness
explainer to rank 6 and failing `G1-freshness-doc`.

The 2026-08-01 lesson was "index the advertisement, not the implementation" — index a
skill's description card rather than its body. This is the inverse and the same principle:
**do not index the index.**
"""


def is_excluded(path: Path, *, root: Path) -> bool:
    """True if ``path`` is denylisted — by directory (privacy) or by name (meta doc)."""
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return True  # outside the root: refuse rather than guess
    if path.name in EXCLUDED_FILES:
        return True
    return any(rel == d or rel.startswith(f"{d}/") for d in EXCLUDED_DIRS)


def collect_sources(root: Path, globs: tuple[str, ...] | None = None) -> list[Path]:
    """Every ingestible file under ``root``, denylist applied, in stable order."""
    if globs is None:
        globs = default_globs()
    found: set[Path] = set()
    for pattern in globs:
        found.update(p for p in root.glob(pattern) if p.is_file() and not is_excluded(p, root=root))
    return sorted(found)


SKILLS_GLOB = ".claude/skills/*/SKILL.md"


def collect_skills(root: Path) -> list[tuple[Path, str]]:
    """(path, skill_name) for every skill. Keyed on the DIRECTORY, not the filename.

    Every skill file is called ``SKILL.md``; keying on ``path.name`` would collapse
    all of them into one source and make retrieval meaningless.
    """
    found = [
        (p, p.parent.name)
        for p in root.glob(SKILLS_GLOB)
        if p.is_file() and not is_excluded(p, root=root) and not p.parent.name.startswith("_")
    ]
    return sorted(found, key=lambda pair: pair[1])


def make_backend(kind: str = "memory") -> VectorBackend:
    """Build a backend. ``memory`` is lexical and dependency-free; ``chroma`` embeds."""
    if kind == "memory":
        return InMemoryBackend()
    if kind == "chroma":
        from .chroma import ChromaBackend

        return ChromaBackend()
    raise ValueError(f"unknown backend {kind!r} (expected 'memory' or 'chroma')")


def build_store(
    root: Path,
    globs: tuple[str, ...] | None = None,
    *,
    collection: str = "corpus",
    backend: str = "memory",
) -> Store:
    store = Store(make_backend(backend))
    for source in collect_sources(root, globs):
        store.ingest(source, collection=collection)
    return store


def skill_card(path: Path) -> str:
    """The skill's advertised identity: its frontmatter ``name`` + ``description``.

    Indexing the whole SKILL.md is measurably worse. Body prose from an unrelated
    skill can outrank the right skill's description — measured: the intent "make my
    CV match a job posting" ranked `cv-aligner` **third**, behind `checkpoint`, when
    whole files were indexed. A tool catalogue is a set of *advertisements*, so index
    the advertisement. This is the same reason MCP tool discovery embeds tool
    descriptions rather than implementations.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text[:600]

    _, _, rest = text.partition("---")
    front, _, _ = rest.partition("\n---")
    fields: list[str] = []
    in_field = False
    for raw in front.splitlines():
        if raw.startswith(("name:", "description:")):
            in_field = True
            _, _, value = raw.partition(":")
            fields.append(value.strip())
        elif in_field and raw[:1] in (" ", "\t") and raw.strip():
            fields.append(raw.strip())  # folded YAML continuation
        elif raw.strip() and not raw.startswith(" "):
            in_field = False
    return " ".join(f for f in fields if f) or text[:600]


def lookup_skill(
    root: Path,
    query: str,
    *,
    k: int = 3,
    use_rerank: bool = True,
    scorer: Scorer | None = None,
    store: Store | None = None,
) -> tuple[list[Hit], str]:
    """Find skills by intent, reranking when a cross-encoder is available.

    Returns (hits, stage) where stage is "reranked" or "retrieval-only", so the caller
    can *say* which path produced the answer rather than leaving it ambiguous.

    Measured on this catalogue (13 skills x 10 intents): retrieval-only top-1 = 5/10,
    reranked = 8/10. Retrieval recall is already 10/10 at top-3, so the second stage is
    doing ranking work, not finding work.

    Degrades visibly, never silently: without the ``rerank`` extra it returns
    retrieval-only order and says so. A reranker that quietly does nothing is worse
    than no reranker, because the caller believes the ranking was improved.
    """
    active = store if store is not None else build_skills_store(root)
    hits = active.query(query, collection="skills", k=max(k, RERANK_FETCH_K))
    if not use_rerank or not hits:
        return hits[:k], "retrieval-only"

    if scorer is None:
        try:
            from .rerank import cross_encoder_scorer

            scorer = cross_encoder_scorer()
        except Exception:
            return hits[:k], "retrieval-only (rerank extra not installed)"

    from .rerank import rerank

    return rerank(query, hits, scorer=scorer, top_k=k), "reranked"


def build_skills_store(root: Path, *, backend: str = "chroma", collection: str = "skills") -> Store:
    """Index the skill catalogue for intent-based lookup (semantic tool discovery).

    Defaults to the embedding backend on purpose: the whole point is matching an
    intent phrased in words the skill never uses, which lexical ranking cannot do.
    One chunk per skill — its card — so skills compete as units, not as paragraphs.
    """
    store = Store(make_backend(backend))
    for path, skill_name in collect_skills(root):
        store.ingest_text(skill_card(path), source=skill_name, collection=collection)
    return store


def run_corpus_gate(root: Path, globs: tuple[str, ...] | None = None, goldens=None) -> tuple[GateResult, int]:
    """Build the index and score the goldens. Returns (result, exit_code).

    ``globs``/``goldens`` are injectable so the gate can be exercised against a throwaway corpus
    — the tests must not depend on whichever profile the developer happens to have configured.

    Four states, deliberately distinguished — the whole point of this gate is that its verdict
    means what it says:

    ==========================  ========  ====  ==================================================
    Profile state               status    exit  Why
    ==========================  ========  ====  ==================================================
    no globs, no goldens        SKIPPED      0  RAG lane not in use. Nothing is claimed, so
                                                nothing is broken. Exit 0 keeps CI honest on a
                                                fresh clone rather than permanently red.
    globs, no goldens           SKIPPED      1  You configured a corpus, so you believe retrieval
                                                is being checked. It is not. This is the defect
                                                that shipped as ``PASS goldens 0/0``.
    goldens, no matching files  FAIL         1  Real misconfiguration: assertions with no corpus.
    both                        PASS/FAIL  0/1  The actual evaluation.
    ==========================  ========  ====  ==================================================
    """
    resolved = globs if globs is not None else default_globs()
    selected = list(goldens) if goldens is not None else list(profile_goldens())
    sources = collect_sources(root, resolved)

    if not resolved and not selected:
        return GateResult(status="SKIPPED", detail="no `rag.corpus_globs` or `rag.goldens` configured"), 0

    if not sources:
        return (
            GateResult(status="FAIL", detail=f"no corpus files matched {resolved or '(none configured)'} under {root}"),
            1,
        )

    # Zero goldens must NEVER be a pass. `EvalReport.passed` is `all([])`, which is True, so a
    # profile with a corpus but no `rag.goldens` printed "PASS goldens 0/0" and exited 0 — a
    # BLOCKING gate reporting green having asserted nothing. That is the cov=n/a mistake in a new
    # costume, and it shipped even though `evalset.run_gate()` already guards against it: this
    # CLI path called `evaluate()` directly and walked straight past that guard.
    if not selected:
        return (
            GateResult(
                status="SKIPPED",
                detail=(
                    f"corpus has {len(sources)} file(s) but no `rag.goldens` are configured — "
                    "retrieval quality was NOT checked"
                ),
            ),
            1,
        )

    report = evaluate(build_store(root, resolved), collection="corpus", goldens=selected)
    status = "PASS" if report.passed else "FAIL"
    detail = f"{report.summary()} over {len(sources)} source file(s)"
    return GateResult(status=status, detail=detail), 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(prog="rag", description="RAG lane over the local corpus.")
    ap.add_argument("--root", default=".", help="repo root to ingest from")
    sub = ap.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("eval", help="run the golden-question gate (exit 1 on regression)")
    ev.add_argument(
        "--glob",
        action="append",
        dest="globs",
        help="override rag.corpus_globs (repeatable) — lets the gate be exercised without a profile",
    )
    q = sub.add_parser("query", help="ad-hoc retrieval")
    q.add_argument("text")
    q.add_argument("-k", type=int, default=5)
    q.add_argument("--backend", choices=("memory", "chroma"), default="memory")
    s = sub.add_parser("skill", help="find the right skill by intent (semantic + rerank)")
    s.add_argument("text")
    s.add_argument("-k", type=int, default=3)
    s.add_argument("--no-rerank", action="store_true", help="skip the cross-encoder second stage")
    args = ap.parse_args(argv)

    root = Path(args.root)

    if args.command == "eval":
        result, code = run_corpus_gate(root, globs=tuple(args.globs) if args.globs else None)
        print(f"{result.status}  rag-goldens  {result.detail}")
        return code

    if args.command == "skill":
        hits, stage = lookup_skill(root, args.text, k=args.k, use_rerank=not args.no_rerank)
        for hit in hits:
            shown = hit.rerank_score if hit.rerank_score is not None else hit.score
            print(f"{shown:.3f}  {hit.source}")
        print(f"({stage})")
        return 0

    for hit in build_store(root, backend=args.backend).query(args.text, collection="corpus", k=args.k):
        print(f"{hit.score:.3f}  {hit.chunk_id}  {hit.text[:90]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
