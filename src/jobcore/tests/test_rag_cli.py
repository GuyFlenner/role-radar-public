"""The corpus gate and its PII denylist.

The denylist tests are the important ones. This repo's corpus root holds tailored
CVs, recruiter correspondence and the candidate's private/legal material; an ingestion
path that quietly widened would be a **privacy defect**, not a bug. Memory:
`feedback_private_hearing_scope` — the same test the `job-tracking/hearing/` gitignore
rule applies, enforced in code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.jobcore.rag.cli import (
    EXCLUDED_DIRS,
    collect_sources,
    is_excluded,
    lookup_skill,
    main,
    run_corpus_gate,
)
from src.jobcore.rag.goldens import Golden
from src.jobcore.rag.store import InMemoryBackend, Store


class TestDenylist:
    @pytest.mark.parametrize(
        "candidate",
        [
            "job-tracking/hearing/statement.md",
            "job-tracking/hearing/nested/deep.md",
            "job-tracking/_drafts/NOA_REPLY_2026-07-30.md",
            "job-tracking/_drafts/RAW_recruiter_emails_2026-07-25.md",
            "positions/grip/CV_TAILORED.md",
            "positions/armis-ai-eng-tl/LEAD.md",
            "_archive/old.md",
        ],
    )
    def test_private_paths_are_excluded(self, candidate: str) -> None:
        assert is_excluded(Path(candidate), root=Path(".")) is True

    @pytest.mark.parametrize(
        "candidate",
        ["architecture-workflows/ACCURACY_CARD.md", "docs/superpowers/specs/x.md", "README.md"],
    )
    def test_public_paths_are_not_excluded(self, candidate: str) -> None:
        assert is_excluded(Path(candidate), root=Path(".")) is False

    def test_paths_outside_the_root_are_refused(self, tmp_path: Path) -> None:
        """Refuse rather than guess — a traversal must not become an ingest."""
        assert is_excluded(Path("C:/Windows/system.ini"), root=tmp_path) is True

    def test_denylist_covers_every_private_dir_we_know_about(self) -> None:
        """Tripwire: if someone trims EXCLUDED_DIRS, this fails loudly."""
        for required in ("job-tracking/hearing", "job-tracking/_drafts", "positions"):
            assert required in EXCLUDED_DIRS

    def test_real_repo_scan_leaks_nothing_private(self) -> None:
        """End-to-end over the actual working tree, not a fixture."""
        leaked = [
            str(p)
            for p in collect_sources(Path("."))
            if any(part in str(p).replace("\\", "/") for part in ("hearing", "_drafts", "positions/"))
        ]
        assert leaked == [], f"private material reached the index: {leaked}"


class TestCorpusGate:
    """Self-contained: the gate is proven against a corpus these tests build.

    An earlier version asserted against "the real corpus", which meant the developer's own
    private notes. That made the result depend on whose machine it ran on, and under test
    isolation (no profile) it simply failed. A gate's own tests must not need a person.
    """

    @staticmethod
    def _corpus(root: Path) -> None:
        (root / "freshness.md").write_text(
            "# Freshness\n\nRe-ingesting a source replaces its chunks so stale text cannot survive.\n",
            encoding="utf-8",
        )
        (root / "privacy.md").write_text(
            "# Privacy\n\nPersonal data lives outside the repo in PROFILE_DIR and is gitignored.\n",
            encoding="utf-8",
        )

    _GOLDENS = (
        Golden(id="C1", query="stale text after re-ingesting", must_retrieve=("freshness.md",), k=5),
        Golden(id="C2", query="where does personal data live", must_retrieve=("privacy.md",), k=5),
    )

    def test_gate_passes_on_a_healthy_corpus(self, tmp_path: Path) -> None:
        self._corpus(tmp_path)
        result, code = run_corpus_gate(tmp_path, globs=("*.md",), goldens=self._GOLDENS)
        assert code == 0, f"corpus gate regressed: {result.detail}"
        assert result.status == "PASS"

    def test_gate_fails_when_a_golden_regresses(self, tmp_path: Path) -> None:
        """The gate must actually be capable of failing, or it proves nothing."""
        self._corpus(tmp_path)
        impossible = (Golden(id="X", query="stale text", must_retrieve=("does-not-exist.md",), k=5),)
        result, code = run_corpus_gate(tmp_path, globs=("*.md",), goldens=impossible)
        assert code == 1 and result.status == "FAIL"

    def test_empty_root_fails_rather_than_passing_vacuously(self, tmp_path: Path) -> None:
        """No corpus must be a FAIL. A gate with nothing to check is not a pass."""
        result, code = run_corpus_gate(tmp_path, globs=("*.md",))
        assert code == 1
        assert result.status == "FAIL"
        assert "no corpus files matched" in result.detail

    def test_cli_eval_returns_one_when_a_configured_corpus_matches_nothing(self, tmp_path: Path) -> None:
        """Globs are configured but match no files → real misconfiguration, exit 1.

        This test used to be `..._on_an_empty_root` and asserted exit 1 for an empty root with
        *nothing configured*. That conflated two different states. An unconfigured RAG lane is
        not a failure — nobody claimed anything — and making it exit 1 would leave CI
        permanently red on a fresh clone, which is how a gate gets disabled by whoever is
        unblocking the build. See `run_corpus_gate`'s four-state table.
        """
        assert main(["--root", str(tmp_path), "eval", "--glob", "notes/*.md"]) == 1

    def test_cli_eval_returns_zero_when_the_rag_lane_is_not_configured(self, tmp_path: Path) -> None:
        assert main(["--root", str(tmp_path), "eval"]) == 0


class TestSkillLookupIsWired:
    """A reranker nothing calls is a demo, not a capability.

    Caught by audit on 2026-08-02: `rerank.py` was tested but no product path invoked
    it — the 5/10 -> 8/10 improvement lived only inside a test. These assert the CLI
    lookup path actually applies the second stage, and reports which stage it used.
    """

    @staticmethod
    def _store() -> Store:
        store = Store(InMemoryBackend())
        for name, card in (
            ("cv-aligner", "tailor the master CV to a job description"),
            ("checkpoint", "save session state to memory for a fresh session"),
            ("job-radar", "scan boards for new roles"),
        ):
            store.ingest_text(card, source=name, collection="skills")
        return store

    # The query must retrieve BOTH candidates, or the reranker has nothing to reorder
    # and the test proves nothing. An earlier version asked for "tailor the master CV",
    # which shares no token with the checkpoint card, so lexical retrieval never
    # returned it — the assertion failed for the wrong reason.
    MIXED_QUERY = "save the master CV session"

    def test_rerank_reorders_and_reports_the_stage(self) -> None:
        """An injected scorer must actually change the order the caller receives."""

        def prefer_checkpoint(query: str, passage: str) -> float:
            return 1.0 if "session state" in passage else 0.0

        hits, stage = lookup_skill(Path("."), self.MIXED_QUERY, k=2, scorer=prefer_checkpoint, store=self._store())

        assert stage == "reranked"
        assert [h.source for h in hits][:1] == ["checkpoint"], (
            f"the injected scorer's preference was not applied: {[h.source for h in hits]}"
        )
        assert hits[0].rerank_score is not None, "rerank scores must be visible to the caller"

    def test_no_rerank_flag_returns_retrieval_order(self) -> None:
        hits, stage = lookup_skill(Path("."), self.MIXED_QUERY, k=2, use_rerank=False, store=self._store())

        assert stage == "retrieval-only"
        assert hits[0].source != "checkpoint" or hits[0].rerank_score is None
        assert all(h.rerank_score is None for h in hits)

    def test_missing_extra_degrades_visibly_not_silently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The caller must be told the ranking was NOT improved."""
        import importlib

        # `src.jobcore.rag.rerank` resolves to the re-exported *function* (the package
        # __init__ exports it), so patch the module object explicitly.
        rerank_mod = importlib.import_module("src.jobcore.rag.rerank")

        def explode(*_a: object, **_k: object) -> object:
            raise RuntimeError("rerank extra absent")

        monkeypatch.setattr(rerank_mod, "cross_encoder_scorer", explode)

        hits, stage = lookup_skill(Path("."), self.MIXED_QUERY, k=2, store=self._store())

        assert "retrieval-only" in stage
        assert "not installed" in stage, f"the degradation must be stated, got {stage!r}"
        assert hits, "degrading must still return retrieval results"

    def test_empty_result_does_not_crash_the_rerank_path(self) -> None:
        hits, stage = lookup_skill(Path("."), "zzzz-no-match-zzzz", k=2, store=self._store())
        assert hits == []
        assert stage == "retrieval-only"


class TestZeroGoldensIsNeverAPass:
    """Regression for the worst defect this repo has shipped.

    `EvalReport.passed` is `all([])` → True, so a profile with a corpus but no `rag.goldens`
    produced `PASS rag-goldens goldens 0/0 passed` and exit 0 — a BLOCKING gate reporting green
    having asserted nothing, in the repo whose headline rule is "a skipped gate is not a green
    gate". `evalset.run_gate()` already guarded this; the CLI path called `evaluate()` directly
    and bypassed the guard.
    """

    def test_corpus_with_no_goldens_does_not_pass(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("# Doc\n\nSome content.\n", encoding="utf-8")
        result, code = run_corpus_gate(tmp_path, globs=("*.md",), goldens=())
        assert code != 0, "a gate that checked nothing must not exit 0"
        assert result.status != "PASS", f"status was {result.status}: {result.detail}"
        assert "NOT checked" in result.detail

    def test_cli_eval_exits_nonzero_with_a_corpus_but_no_goldens(self, tmp_path: Path, monkeypatch) -> None:
        """End-to-end through the exact path verify_all.sh invokes."""
        (tmp_path / "doc.md").write_text("# Doc\n\nSome content.\n", encoding="utf-8")
        prof = tmp_path / "prof"
        prof.mkdir()
        (prof / "profile.json").write_text(
            '{"schema_version":"1.0","identity":{"name":"X"},"skills":{"keywords":["a"]},'
            '"rag":{"corpus_globs":["*.md"]}}',
            encoding="utf-8",
        )
        monkeypatch.setenv("PROFILE_DIR", str(prof))
        assert main(["--root", str(tmp_path), "eval"]) != 0


class TestCiFixtureIsUsableOnAFreshClone:
    """The CI RAG step must assert something on a machine that is not this one.

    `.gitignore`'s bare `profile.json` rule matches at any depth and silently excluded the
    fixture profile from the first commit. Locally the gate scored 3/3 — because the untracked
    file was sitting in the working tree. On a fresh clone CI would have found the notes but no
    profile, reported SKIPPED, and exited 0: a step that reads as a passing gate while asserting
    nothing. Presence on disk is not the property that matters; being *tracked* is.
    """

    FIXTURE = Path("profiles/_fixtures/rag_demo")

    def test_ci_fixture_profile_is_tracked_by_git(self) -> None:
        from ..csv_ingest import REPO_ROOT

        out = subprocess.run(
            ["git", "ls-files", (self.FIXTURE / "profile.json").as_posix()],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert out.stdout.strip(), (
            "profiles/_fixtures/rag_demo/profile.json is NOT tracked by git — CI will find no "
            "profile, the RAG gate will report SKIPPED, and the step will pass vacuously"
        )

    def test_ci_fixture_notes_are_tracked_by_git(self) -> None:
        from ..csv_ingest import REPO_ROOT

        out = subprocess.run(
            ["git", "ls-files", (self.FIXTURE / "notes").as_posix()],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert len(out.stdout.split()) >= 3, f"expected the fixture corpus to be tracked, got: {out.stdout!r}"

    def test_the_exact_ci_invocation_scores_real_goldens(self, monkeypatch) -> None:
        """Run what ci.yml runs, and assert it actually evaluated — not that it merely exited 0."""
        from ..csv_ingest import REPO_ROOT

        monkeypatch.setenv("PROFILE_DIR", str(REPO_ROOT / self.FIXTURE))
        result, code = run_corpus_gate(REPO_ROOT / self.FIXTURE)
        assert code == 0, f"{result.status}: {result.detail}"
        assert result.status == "PASS", f"{result.status}: {result.detail}"
        assert "0/0" not in result.detail, f"gate asserted nothing: {result.detail}"
        assert "3/3" in result.detail, result.detail


class TestAGoldenThatAssertsNothingIsNotAPass:
    """The third nesting of one bug, and the one that survived every earlier fix.

    Layer 1: zero goldens -> `all([])` -> PASS. Fixed.
    Layer 2: the CI fixture untracked -> SKIPPED/0, a no-op step reading as green. Fixed.
    Layer 3 (here): goldens PRESENT but each asserting nothing. `score_golden` collects failures
    from four *optional* assertions; with none set it returns zero failures and the golden
    "passes". Three of them produced `PASS rag-goldens goldens 3/3 passed` over a corpus the
    gate never tested — and it survived a clean-clone check, because a vacuous pass is
    indistinguishable from a real one from the outside.

    Root cause: the fixture used `expect_source`, a key that has never existed in this schema.
    The constructor silently dropped it, turning a golden its author believed was strict into
    one that asserted nothing. So the fix is in two places — reject unknown keys at parse time,
    and refuse to call an assertion-free golden a pass at score time.
    """

    def test_a_golden_with_no_assertions_fails(self) -> None:
        from ..rag.goldens import Golden, score_golden

        outcome = score_golden(Golden(id="empty", query="anything"), hits=[])
        assert outcome.passed is False
        assert any("no assertions" in f for f in outcome.failures), outcome.failures

    def test_asserts_something_recognises_each_assertion_kind(self) -> None:
        from ..rag.goldens import Golden

        assert not Golden(id="x", query="q").asserts_something
        assert Golden(id="x", query="q", must_retrieve=("a.md",)).asserts_something
        assert Golden(id="x", query="q", must_not_retrieve=("a.md",)).asserts_something
        assert Golden(id="x", query="q", top_source="a.md").asserts_something
        assert Golden(id="x", query="q", min_score=0.1).asserts_something

    def test_unknown_profile_golden_key_raises_instead_of_being_dropped(self, tmp_path, monkeypatch) -> None:
        """`expect_source` was invented; silently ignoring it is what made the gate lie."""
        import json

        import pytest as _pytest

        from ..rag.goldens import profile_goldens

        prof = tmp_path / "p"
        prof.mkdir()
        (prof / "profile.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "identity": {"name": "X"},
                    "skills": {"keywords": ["a"]},
                    "rag": {"corpus_globs": ["*.md"], "goldens": [{"query": "q", "expect_source": "a.md"}]},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PROFILE_DIR", str(prof))
        with _pytest.raises(ValueError, match="unknown key"):
            profile_goldens()

    def test_the_shipped_ci_fixture_goldens_all_assert_something(self) -> None:
        """The fixture is the gate's only real assertion in CI. It must not be empty again."""
        import json

        from ..csv_ingest import REPO_ROOT

        data = json.loads((REPO_ROOT / "profiles/_fixtures/rag_demo/profile.json").read_text(encoding="utf-8"))
        goldens = data["rag"]["goldens"]
        assert goldens, "the CI fixture has no goldens"
        for g in goldens:
            assert g.get("must_retrieve") or g.get("top_source") or g.get("min_score") or g.get("must_not_retrieve"), (
                f"CI fixture golden {g.get('id')!r} asserts nothing — the gate would pass vacuously"
            )

    def test_the_ci_gate_fails_when_a_golden_points_at_a_missing_file(self, tmp_path, monkeypatch) -> None:
        """End-to-end proof the gate can fail, through the exact ci.yml path."""
        import json
        import shutil

        from ..csv_ingest import REPO_ROOT

        fixture = REPO_ROOT / "profiles/_fixtures/rag_demo"
        shutil.copytree(fixture / "notes", tmp_path / "notes")
        data = json.loads((fixture / "profile.json").read_text(encoding="utf-8"))
        data["rag"]["goldens"] = [{"id": "bogus", "query": "postgres", "must_retrieve": ["NOPE.md"]}]
        (tmp_path / "profile.json").write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setenv("PROFILE_DIR", str(tmp_path))
        assert main(["--root", str(tmp_path), "eval"]) == 1
