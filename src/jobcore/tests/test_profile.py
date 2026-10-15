"""Profile loader, validation, and the isolation guarantees that let two people share this repo.

The privacy tests at the bottom are not decoration. This platform exists because a shared repo
would otherwise expose each user's CV, pipeline, salary bands and rejections to the other. These
assertions are the mechanical part of that promise; the rest is `.gitignore` and review.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.jobcore import profile as P
from src.jobcore import scoring

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "profiles" / "_fixtures"


# --- loading ------------------------------------------------------------------


def test_missing_profile_yields_empty_skeleton_not_a_crash():
    """A fresh clone with nothing configured must still import and run."""
    prof = P.load(Path("does-not-exist.json"))
    assert prof.raw["schema_version"] == P.SCHEMA_VERSION
    assert prof.keywords == []
    assert prof.has_facts is False


def test_missing_profile_raises_when_required():
    with pytest.raises(P.ProfileError, match="no profile found"):
        P.load(Path("does-not-exist.json"), required=True)


def test_malformed_json_names_the_file():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "profile.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(P.ProfileError, match="not valid JSON"):
            P.load(bad)


def test_partial_profile_is_merged_over_the_skeleton(tmp_path):
    """A user who only sets a couple of fields must not lose every default."""
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"identity": {"name": "Only Name"}}), encoding="utf-8")
    prof = P.load(p)
    assert prof.name == "Only Name"
    assert prof.geo_mode == "preferred"  # skeleton default survived


def test_explicit_path_beats_profile_dir(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "profile.json").write_text(json.dumps({"identity": {"name": "From PROFILE_DIR"}}), encoding="utf-8")
    monkeypatch.setenv("PROFILE_DIR", str(other))
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({"identity": {"name": "Explicit"}}), encoding="utf-8")
    assert P.load(explicit).name == "Explicit"


# --- validation ---------------------------------------------------------------


def test_shipped_template_and_fixtures_all_validate():
    for f in [REPO / "profiles" / "_template.json", *sorted(FIXTURES.glob("*.json"))]:
        assert P.validate(P.load(f)) == [], f"{f.name}: {P.validate(P.load(f))}"


def test_required_geo_with_no_locations_is_rejected(tmp_path):
    """The misconfiguration that would silently drop every single role."""
    p = tmp_path / "profile.json"
    p.write_text(
        json.dumps(
            {
                "identity": {"name": "X"},
                "skills": {"keywords": ["a"]},
                "geo": {"mode": "required", "preferred_locations": []},
            }
        ),
        encoding="utf-8",
    )
    assert any("drops every role" in x for x in P.validate(P.load(p)))


def test_unknown_geo_mode_is_rejected(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"geo": {"mode": "somewhere-nice"}}), encoding="utf-8")
    assert any("geo.mode" in x for x in P.validate(P.load(p)))


def test_empty_keywords_is_reported():
    assert any("skills.keywords is empty" in x for x in P.validate(P.load(Path("nope.json"))))


def test_boost_keywords_must_be_a_subset(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text(
        json.dumps({"identity": {"name": "X"}, "skills": {"keywords": ["react"], "boost_keywords": ["vue"]}}),
        encoding="utf-8",
    )
    assert any("boost_keywords" in x for x in P.validate(P.load(p)))


# --- the point of the whole exercise -------------------------------------------


def test_two_profiles_rank_the_same_two_jobs_oppositely(backend_profile, frontend_profile):
    """The one test that proves this is a platform and not one person's tool.

    Under the old hardcoded constants the Berlin frontend role scored 1 and was dropped by the
    geo gate before scoring ever happened.
    """
    berlin_fe = {
        "title": "Senior Frontend Engineer",
        "location": "Berlin",
        "snippet": "React, TypeScript, accessibility",
    }
    tlv_be = {"title": "Backend Team Lead", "location": "Tel Aviv", "snippet": "Python, Kafka, Kubernetes, AI agents"}

    assert scoring.score(berlin_fe, frontend_profile) > scoring.score(berlin_fe, backend_profile)
    assert scoring.score(tlv_be, backend_profile) > scoring.score(tlv_be, frontend_profile)

    assert scoring.is_relevant(berlin_fe, frontend_profile) is True
    assert scoring.location_allowed(berlin_fe, frontend_profile)[0] is True
    # The backend profile uses a hard commute band, so Berlin is out of scope for it.
    assert scoring.location_allowed(berlin_fe, backend_profile)[0] is False


def test_geo_preferred_mode_never_drops(frontend_profile):
    far = {"title": "Frontend Engineer", "location": "Reykjavik"}
    allowed, preferred = scoring.location_allowed(far, frontend_profile)
    assert allowed is True and preferred is False


# --- privacy guarantees --------------------------------------------------------


#: The ONLY paths under positions/ or job-tracking/ allowed to be tracked. Scaffolding that
#: documents the expected layout — never anyone's actual pipeline. Every entry must live under a
#: `_`-prefixed directory, which `test_scaffolding_allowlist_cannot_name_a_real_company` enforces:
#: a real company slug is never `_`-prefixed, so the allowlist cannot quietly grow to admit one.
SCAFFOLDING_ALLOWLIST = ("positions/_template/",)


def test_no_profile_dir_content_is_tracked_by_git():
    """PROFILE_DIR holds a real person's CV and pipeline. It must never enter this repo."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False).stdout.splitlines()
    offenders = [
        f
        for f in out
        if (re.search(r"(^|/)(positions|job-tracking)/", f) or re.search(r"(^|/)cv(_\w+)?\.(md|pdf)$", f, re.I))
        and not f.startswith(SCAFFOLDING_ALLOWLIST)
    ]
    assert offenders == [], f"personal data is tracked in the shared repo: {offenders}"


def test_scaffolding_allowlist_cannot_name_a_real_company():
    """Guard the exception, not just the rule.

    An allowlist is how a privacy firewall erodes: one entry gets added to unblock a commit, and
    the next reader sees a precedent. Every entry must be a `_`-prefixed scaffolding directory.
    """
    for entry in SCAFFOLDING_ALLOWLIST:
        leaf = entry.rstrip("/").split("/")[-1]
        assert leaf.startswith("_"), f"allowlist entry {entry!r} is not scaffolding — it names a real slug"


def test_allowlisted_scaffolding_contains_no_personal_data():
    """Whatever the allowlist admits is READ, not trusted."""
    personal = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|₪\s?\d")
    for entry in SCAFFOLDING_ALLOWLIST:
        for path in (REPO / entry).rglob("*"):
            if not path.is_file():
                continue
            found = personal.findall(path.read_text(encoding="utf-8", errors="replace"))
            assert found == [], f"{path} contains personal data: {found}"


def test_fixtures_contain_no_real_looking_email():
    for f in FIXTURES.glob("*.json"):
        text = f.read_text(encoding="utf-8")
        found = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        assert found == [], f"{f.name} contains an email address: {found}"


def test_fixtures_are_marked_synthetic():
    """A future reader must not mistake a fixture for a real person's profile."""
    for f in FIXTURES.glob("*.json"):
        assert "SYNTHETIC" in f.read_text(encoding="utf-8"), f"{f.name} lacks the SYNTHETIC marker"


def test_msys_style_profile_dir_is_translated(tmp_path, monkeypatch):
    r"""Regression: `echo PROFILE_DIR=$HOME/x` in Git Bash on Windows writes `/c/Users/...`.

    Python resolved that to `C:\c\Users\...`, found nothing, and silently fell back to
    defaults — every role scoring flat, with no error at all. Losing the profile must never be
    quiet.
    """
    if sys.platform != "win32":
        pytest.skip("MSYS path translation only applies on Windows")
    real = tmp_path / "prof"
    real.mkdir()
    (real / "profile.json").write_text(json.dumps({"identity": {"name": "Found Me"}}), encoding="utf-8")

    drive, rest = str(real)[0], str(real)[2:].replace("\\", "/")
    monkeypatch.setenv("PROFILE_DIR", f"/{drive.lower()}{rest}")
    assert P.load().name == "Found Me"


def test_a_posix_single_letter_path_is_left_alone_off_windows(tmp_path, monkeypatch):
    """The other half of the same rule — and the bug the first Linux CI run caught.

    On Linux `/c/Users/foo` is an ordinary absolute path. The MSYS translation rewrote it to
    `C:/Users/foo`, a RELATIVE path resolved against the cwd, so a Linux user with a `/c` or `/d`
    mount had their profile silently not found. That is exactly the failure `_normalize_dir`
    exists to prevent, inflicted on the other platform — and it was invisible locally because
    every local run was on Windows.
    """
    if sys.platform == "win32":
        pytest.skip("on Windows a leading /c/ genuinely is an MSYS path")
    target = tmp_path / "c" / "profiles"
    target.mkdir(parents=True)
    (target / "profile.json").write_text(json.dumps({"identity": {"name": "Posix Me"}}), encoding="utf-8")
    monkeypatch.setenv("PROFILE_DIR", str(target))
    assert P.load().name == "Posix Me"


def test_normalize_dir_does_not_mangle_a_posix_path_off_windows():
    """Unit-level, no filesystem: the translation must fire on Windows and only there."""
    got = str(P._normalize_dir("/c/Users/foo")).replace("\\", "/")
    if sys.platform == "win32":
        assert got == "C:/Users/foo"
    else:
        assert got == "/c/Users/foo", "a legitimate POSIX absolute path was rewritten"


class TestRagAndCsvSectionsExistInTheSchema:
    """Both lanes read profile keys that the schema never declared.

    `rag.corpus_globs`/`rag.goldens` and `csv.pattern`/`csv.sender_filter` were read at runtime
    but absent from `_skeleton()`, the template and the fixtures — so `profile validate` could
    not see a misconfiguration, and `profile show` did not reveal the knobs existed.
    """

    def test_skeleton_declares_both_sections(self) -> None:
        from ..profile import _skeleton

        sk = _skeleton()
        assert sk["rag"] == {"corpus_globs": [], "goldens": []}
        assert set(sk["csv"]) == {"pattern", "sender_filter"}

    def test_template_and_fixtures_declare_both_sections(self) -> None:
        import json
        from pathlib import Path

        from ..csv_ingest import REPO_ROOT

        for name in ("_template.json", "_fixtures/backend_lead_tlv.json", "_fixtures/frontend_ic_berlin.json"):
            p = Path(REPO_ROOT) / "profiles" / name
            data = json.loads(p.read_text(encoding="utf-8"))
            assert "rag" in data and "csv" in data, f"{name} is missing rag/csv"

    def test_a_corpus_with_no_goldens_is_a_validation_problem(self) -> None:
        from ..profile import Profile, _skeleton, validate

        raw = _skeleton()
        raw["identity"]["name"] = "X"
        raw["skills"]["keywords"] = ["python"]
        raw["rag"] = {"corpus_globs": ["notes/*.md"], "goldens": []}
        problems = validate(Profile(raw=raw, path=None))
        assert any("goldens" in p for p in problems), problems

    def test_sender_filter_without_a_pattern_is_a_validation_problem(self) -> None:
        from ..profile import Profile, _skeleton, validate

        raw = _skeleton()
        raw["identity"]["name"] = "X"
        raw["skills"]["keywords"] = ["python"]
        raw["csv"] = {"pattern": "", "sender_filter": "JobsBot"}
        problems = validate(Profile(raw=raw, path=None))
        assert any("csv.pattern" in p for p in problems), problems


class TestShippedTemplatesAndIgnoredData:
    """The scaffolding must ship; the data must not. Both directions need a test.

    `.gitignore` had `positions/` (the directory), and git cannot re-include anything inside an
    excluded directory — so `!positions/_template/` was dead and a new user cloned a platform
    whose per-position layout was undocumented. Fixing that risks the opposite error, so both
    properties are asserted here.
    """

    @staticmethod
    def _tracked(pathspec: str) -> list[str]:
        import subprocess

        from ..csv_ingest import REPO_ROOT

        out = subprocess.run(["git", "ls-files", pathspec], cwd=REPO_ROOT, capture_output=True, text=True)
        return out.stdout.split()

    def test_the_position_layout_template_ships(self) -> None:
        assert self._tracked("positions/_template"), (
            "positions/_template is not tracked — a clone has no documented per-position layout"
        )

    def test_the_profile_template_ships(self) -> None:
        assert self._tracked("profiles/_template.json")

    def test_real_position_data_is_still_ignored(self, tmp_path) -> None:
        """Re-including the template must not re-include anyone's pipeline."""
        import subprocess

        from ..csv_ingest import REPO_ROOT

        for candidate in ("positions/acme-corp/STATUS.md", "positions/acme-corp/CV_TAILORED.md"):
            out = subprocess.run(["git", "check-ignore", "-q", candidate], cwd=REPO_ROOT, capture_output=True)
            assert out.returncode == 0, f"{candidate} would be committable — personal pipeline data"


class TestTrackedContentCarriesNoPrivateMaterial:
    """Path-level privacy is not enough — content leaks too.

    Every existing privacy test asked "is a private FILE tracked?". None asked "does a tracked
    file mention private THINGS?". A clean-clone review found three that did: `claude-skills.lock`
    described a guard as "never read … any שימוע/termination/legal file", which tells any reader
    of this shared repo that the owner is in a pre-termination process; and two `rag/` docstrings
    named the owner's employer and an employer-internal repository.

    None of that is a file in the wrong place. It is the wrong sentence in the right file, and it
    reaches the coworker exactly the same way.
    """

    #: Vocabulary of the private-scope firewall. Category words, not one person's details —
    #: a denylist of specific names would only ever protect the person who wrote it.
    PRIVATE_VOCAB = (
        "שימוע",  # Hebrew: pre-termination hearing
        "pre-termination",
        "termination hearing",
        "disciplinary hearing",
        "severance negotiation",
    )

    @staticmethod
    def _tracked_text_files() -> list[Path]:
        import subprocess

        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False)
        keep = (".md", ".py", ".json", ".sh", ".yml", ".yaml", ".toml", ".lock", ".txt", ".example")
        return [REPO / f for f in out.stdout.split() if f.endswith(keep)]

    def test_no_tracked_file_mentions_a_private_legal_process(self) -> None:
        offenders = []
        for path in self._tracked_text_files():
            if path.name == "test_profile.py":
                continue  # this file necessarily names the vocabulary it screens for
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for word in self.PRIVATE_VOCAB:
                if word.lower() in text.lower():
                    offenders.append(f"{path.relative_to(REPO)}: {word!r}")
        assert offenders == [], (
            "tracked files reference a private legal process — this is a shared repo:\n  " + "\n  ".join(offenders)
        )

    def test_the_screen_would_actually_catch_something(self, tmp_path) -> None:
        """Guard the guard: prove the vocabulary match works, not just that nothing matched."""
        sample = "Never read the שימוע folder."
        assert any(w.lower() in sample.lower() for w in self.PRIVATE_VOCAB)

    def test_no_tracked_file_carries_a_personal_email(self) -> None:
        """Third-party and owner PII alike — the coworker reads all of it."""
        email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        allowed = re.compile(r"(example|test|your|noreply|user)[@.]|@(example|test)\.", re.I)
        offenders = []
        for path in self._tracked_text_files():
            if path.name == "test_profile.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for found in email.findall(text):
                if not allowed.search(found):
                    offenders.append(f"{path.relative_to(REPO)}: {found}")
        assert offenders == [], "tracked files contain real-looking email addresses:\n  " + "\n  ".join(offenders)


class TestNormalizeDirBothPlatformBranches:
    """Exercise BOTH branches on ANY OS, by testing the pure string transformation.

    Two blind spots stacked here, and the second one is mine:

    1. `_normalize_dir` translated MSYS paths unconditionally. On Linux `/c/Users/foo` is an
       ordinary absolute path and the rewrite made it relative. Every local run was on Windows,
       so only one branch ever executed. The first Linux CI run caught it.

    2. The test I then wrote to close that gap ALSO had a platform blind spot: it asserted on
       `Path.drive`, and `PosixPath("C:/Users/foo").drive` is `""` on Linux — so on Linux both
       branches looked identical and every assertion passed vacuously. The
       `test_the_two_branches_genuinely_differ` guard is what caught it, failing with
       `assert '' != ''`. That is precisely why the guard exists.

    The fix for both: `translate_msys_path` takes `platform` as an argument and returns a `str`,
    so no assertion has to route through a platform-bound `Path`.
    """

    @staticmethod
    def _t(value: str, platform: str) -> str:
        return P.translate_msys_path(value, platform=platform)

    def test_windows_translates_to_a_drive(self):
        assert self._t("/c/Users/foo", "win32") == "C:/Users/foo"

    def test_windows_uppercases_the_drive(self):
        assert self._t("/d/data", "win32") == "D:/data"

    def test_windows_bare_drive_gets_a_root_slash(self):
        assert self._t("/c", "win32") == "C:/"

    def test_windows_leaves_a_normal_path_alone(self):
        assert self._t("C:/already/windows", "win32") == "C:/already/windows"
        assert self._t("/usr/local/share", "win32") == "/usr/local/share"

    def test_linux_leaves_a_posix_path_untouched(self):
        assert self._t("/c/Users/foo", "linux") == "/c/Users/foo"

    def test_linux_leaves_a_single_letter_mount_untouched(self):
        """`/d/projects` on a Linux box is a real path, not a Windows drive."""
        assert self._t("/d/projects", "linux") == "/d/projects"

    def test_darwin_behaves_like_linux(self):
        assert self._t("/c/Users/foo", "darwin") == "/c/Users/foo"

    def test_the_two_branches_genuinely_differ(self):
        """Guard the guard. This assertion has already earned its place twice.

        If the branches ever agree, every other test in this class passes while proving nothing —
        which is exactly what happened on Linux when the assertions went through `Path.drive`.
        """
        assert self._t("/c/Users/foo", "win32") != self._t("/c/Users/foo", "linux"), (
            "the platform guard has no effect — the branches are identical"
        )

    def test_quotes_and_whitespace_are_stripped_on_every_platform(self):
        for plat in ("win32", "linux", "darwin"):
            assert self._t('  "/tmp/x"  ', plat).endswith("/tmp/x")
            assert self._t("  '/tmp/x'  ", plat).endswith("/tmp/x")

    def test_normalize_dir_delegates_to_the_pure_function(self):
        """The wrapper must not reintroduce its own logic."""
        expected = P.translate_msys_path("/c/Users/foo", platform=sys.platform)
        assert P._normalize_dir("/c/Users/foo") == Path(expected).expanduser()
