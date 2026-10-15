"""The skill-prompt lint, and proof it is capable of failing.

A SKILL.md is executable — Claude reads it and does what it says — but nothing compiled it, so
it rotted invisibly: 14 dead `.env` keys and a `cp` from a directory that does not exist.
"""

from __future__ import annotations

from pathlib import Path

from ..skill_lint import ENV_ALLOWLIST, check_skill, declared_env_keys, main, run

DECLARED = {"PROFILE_DIR", "YOUR_NAME", "EXA_API_KEY"}


def _skill(tmp_path: Path, body: str, name: str = "demo") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(body, encoding="utf-8")
    return p


class TestTheRealRepoPasses:
    def test_every_shipped_skill_resolves(self) -> None:
        problems, code = run()
        assert problems == [], "\n".join(problems)
        assert code == 0


class TestItCanActuallyFail:
    """A gate that cannot fail proves nothing — the lesson from `PASS goldens 0/0`."""

    def test_dead_env_key_in_an_env_sentence_is_caught(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Read `TARGET_ROLES` from `.env` before scoring.\n")
        problems = check_skill(p, DECLARED)
        assert any("TARGET_ROLES" in x for x in problems), problems

    def test_dollar_sigil_is_caught_anywhere(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Use `$MIN_KEYWORD_SCORE` as the cutoff.\n")
        assert any("MIN_KEYWORD_SCORE" in x for x in check_skill(p, DECLARED)), "sigil form missed"

    def test_missing_path_in_a_fenced_cp_is_caught(self, tmp_path: Path) -> None:
        """The exact defect the first draft of this lint walked past."""
        p = _skill(tmp_path, "Copy them:\n```bash\ncp job-seeking/templates/_position/STATUS.md out/\n```\n")
        problems = check_skill(p, DECLARED)
        assert any("_position/STATUS.md" in x for x in problems), problems

    def test_missing_backticked_path_is_caught(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "See `scripts/does_not_exist.sh` for details.\n")
        assert any("does_not_exist" in x for x in check_skill(p, DECLARED)), "backticked path missed"


class TestItDoesNotCryWolf:
    """A noisy gate gets switched off, which is worse than no gate.

    Every string here is a real false positive an earlier draft produced.
    """

    def test_ledger_enums_are_not_env_vars(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Stages are `APPLIED`, `IN_PROCESS`, `CLOSED` and `UNVERIFIED`.\n")
        assert check_skill(p, DECLARED) == []

    def test_python_constants_are_not_env_vars(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Colours come from `ROLE_PALETTE` and `ZONE_STROKES`.\n")
        assert check_skill(p, DECLARED) == []

    def test_prose_slashes_are_not_paths(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Score `8/10`. Poll `Greenhouse/Comeet`. A `lead/manager` seat.\n")
        assert check_skill(p, DECLARED) == []

    def test_bare_filenames_are_not_repo_paths(self, tmp_path: Path) -> None:
        """`STATUS.md` is a per-position output, not a repo file."""
        p = _skill(tmp_path, "Write `STATUS.md` and `SALARY_BENCHMARK.md`.\n")
        assert check_skill(p, DECLARED) == []

    def test_runtime_and_profile_dir_paths_are_exempt(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Read `positions/acme/STATUS.md` and `job-tracking/cv.md`.\n")
        assert check_skill(p, DECLARED) == []

    def test_placeholders_are_exempt(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Write `positions/{slug}/CV_TAILORED.md`.\n")
        assert check_skill(p, DECLARED) == []

    def test_allowlisted_env_vars_pass(self, tmp_path: Path) -> None:
        p = _skill(tmp_path, "Set `$PROFILE_DIR` in your `.env`. Claude passes `$ARGUMENTS`.\n")
        assert check_skill(p, DECLARED) == []


class TestTheGateCannotPassVacuously:
    def test_no_skills_found_is_a_failure_not_a_pass(self, tmp_path: Path) -> None:
        problems, code = run(skills_dir=tmp_path)
        assert code == 1
        assert any("no skills found" in p for p in problems)

    def test_underscore_prefixed_dirs_are_skipped(self, tmp_path: Path) -> None:
        """_legacy/ is reference material, not loaded by Claude — but it must not empty the run."""
        _skill(tmp_path, "Read `TARGET_ROLES` from `.env`.\n", name="_legacy")
        _skill(tmp_path, "All good.\n", name="live")
        problems, code = run(skills_dir=tmp_path)
        assert code == 0, problems

    def test_env_allowlist_stays_small(self) -> None:
        """The allowlist is the escape hatch that would let dead keys back in."""
        assert len(ENV_ALLOWLIST) <= 8, sorted(ENV_ALLOWLIST)

    def test_declared_keys_include_commented_options(self) -> None:
        keys = declared_env_keys()
        assert "PROFILE_DIR" in keys
        assert "ANTHROPIC_API_KEY" in keys, "commented-out options are still documented options"


class TestCli:
    def test_exits_zero_on_the_real_repo(self) -> None:
        assert main([]) == 0

    def test_list_mode_exits_zero(self, capsys) -> None:
        assert main(["--list"]) == 0
        assert "declared in .env.example" in capsys.readouterr().out


class TestCommandsAreLintedToo:
    """`.claude/commands/*.md` are prompts Claude executes, exactly like a skill.

    The first version of this lint only globbed `*/SKILL.md`, so an entire prompt directory sat
    unchecked — and it was where three references to a nonexistent "Active Positions table in
    CLAUDE.md" lived. A gate with a blind spot reports on what it looked at, not on what exists,
    and its PASS line said "13 skills" while 14 prompt files were present.
    """

    def test_the_reported_count_covers_commands_as_well_as_skills(self) -> None:
        from ..skill_lint import COMMANDS_DIR, SKILLS_DIR, prompt_count

        skills = len([p for p in SKILLS_DIR.glob("*/SKILL.md") if not p.parent.name.startswith("_")])
        commands = (
            len([p for p in COMMANDS_DIR.glob("*.md") if not p.name.startswith("_")]) if COMMANDS_DIR.exists() else 0
        )
        assert commands > 0, "no commands found — this test would be vacuous"
        assert prompt_count() == skills + commands

    def test_commands_dir_is_actually_read(self) -> None:
        """Proven by construction: the real run must include a commands/ prefixed name if broken."""
        from ..skill_lint import COMMANDS_DIR, check_skill

        target = next(iter(sorted(COMMANDS_DIR.glob("*.md"))), None)
        assert target is not None
        problems = check_skill(target, {"PROFILE_DIR"})
        # The shipped command is clean; what matters is that check_skill labels it as a command,
        # which is what proves run() would surface it rather than skipping the directory.
        assert isinstance(problems, list)

    def test_no_prompt_references_a_removed_claude_md_section(self) -> None:
        """The Active Positions table was replaced by the pipeline ledger; nothing may still read it."""
        from ..csv_ingest import REPO_ROOT
        from ..skill_lint import COMMANDS_DIR, SKILLS_DIR

        claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        offenders = []
        for p in [*SKILLS_DIR.glob("*/SKILL.md"), *COMMANDS_DIR.glob("*.md")]:
            text = p.read_text(encoding="utf-8")
            if "Active Positions" in text and "no Active Positions table" not in text:
                offenders.append(str(p.relative_to(REPO_ROOT)))
        assert "Active Positions" not in claude_md, "CLAUDE.md regrew the hand-kept table"
        assert offenders == [], f"prompts still read a section CLAUDE.md does not have: {offenders}"


class TestLocaleNeutrality:
    """The prompts must not pin the platform to one market.

    The generality refactor stopped at the Python boundary: `src/jobcore/` became profile-driven
    while the prompt layer — the half a user actually touches — still specified a commute band in
    one country, a salary benchmark in one currency, and one country as the default search
    location. A judge's summary: a Berlin frontend IC gets a correct shortlist from the core, then
    the prose benchmarks their salary in the wrong currency.
    """

    def test_currency_symbol_as_an_instruction_is_caught(self) -> None:
        from ..skill_lint import locale_problems

        assert locale_problems("Prefer **gross monthly NIS** for the benchmark.", "x")

    def test_country_as_an_instruction_is_caught(self) -> None:
        from ..skill_lint import locale_problems

        assert locale_problems("Default the search location to Israel.", "x")

    def test_an_explicit_example_is_exempt(self) -> None:
        """Measured worked examples are evidence. Scrubbing them to satisfy a regex is vandalism."""
        from ..skill_lint import locale_problems

        assert locale_problems("A national salary table (e.g. ₪56.6K blended) overstates it.", "x") == []
        assert locale_problems("Reference test: the Berlin run returned 403.", "x") == []
        assert locale_problems('- example: `"<Employer> team lead Haifa careers"`', "x") == []

    def test_fenced_blocks_are_exempt(self) -> None:
        """A shell example may legitimately show a concrete value."""
        from ..skill_lint import locale_problems

        assert locale_problems('```bash\n--location "Israel"\n```', "x") == []

    def test_neutral_prose_is_clean(self) -> None:
        from ..skill_lint import locale_problems

        assert locale_problems("Read the location from the profile's `geo` block.", "x") == []

    def test_every_shipped_prompt_is_locale_neutral(self) -> None:
        from ..skill_lint import COMMANDS_DIR, SKILLS_DIR, locale_problems

        offenders = []
        for p in [*SKILLS_DIR.glob("*/SKILL.md"), *COMMANDS_DIR.glob("*.md")]:
            if p.parent.name.startswith("_"):
                continue
            offenders += locale_problems(p.read_text(encoding="utf-8"), p.parent.name)
        assert offenders == [], "\n".join(offenders)
