# Profile-driven platform — one private repo, two job seekers

**Date:** 2026-08-02
**Owner:** platform maintainers
**Status:** Design → awaiting review
**Decision taken:** the repo stays **private**, shared by **two named users**. Not public
open-source. That lowers the de-personalization bar (no need to scrub every example or comment)
but *raises* the isolation bar — see [Privacy model](#privacy-model-the-part-that-is-not-optional).

## Problem

A second person wants to use this pipeline for her own search. Three independent problems block it,
all verified on 2026-08-02:

**1. A clone leaks the owner's whole job search.** Of 649 tracked files, **469 are personal** — 53
position folders, 97 CV files/PDFs, 26 interview/rejection debriefs with candid self-scores, 58
files containing salary figures. Separately, two recruiter files untracked on 07-30
(`NOA_REPLY_2026-07-30.md`, `recruiter_leads_friend_2026-07-25.md`) are **still reachable in git
history**, carrying third-party emails (two named recruiters' addresses — redacted here on purpose). Untracking
removes a file from the tip, not the past. *(Genuinely clean: `job-tracking/hearing/` was **never**
committed on any branch, and neither were `.env` or `ats_targets.json`.)*

**2. `.env` is decoration — the core never reads it.** `README.md:81` claims *"Everything is driven
by `.env`"*. Verified false: there is **no `load_dotenv` anywhere in `src/`**, and
`TARGET_ROLES`, `PREFERRED_LOCATIONS`, `HOME_CITY`, `REJECT_TITLES`, `MIN_KEYWORD_SCORE` and
`CV_PATH` are **read by nothing**. The only env vars the code touches are the `LLM_*` family and
`YOUR_NAME` (`pdf.py:88`). A new user edits `.env` and the ranking does not change.

**3. One person's career is compiled into the logic.** Not configuration — module-level constants:

| Constant | File | What it hardcodes |
|---|---|---|
| `CENTER_ISRAEL` | `scoring.py:10` | 18 Israeli cities, used as a **hard drop** (`csv_ingest.py:239-243`) — silently discards every Berlin role |
| `KEEP_TITLES` / `EXCLUDE_TITLES` | `scoring.py:31,40` | requires a *leadership* title; a frontend IC fails `is_relevant()` and scores 1 |
| `CV_KEYWORDS` | `scoring.py:52` | this candidate's stack (kafka, rails, mcp, rag, ddd) |
| `_ABOVE_BAND` | `scoring.py:126` | this candidate's seniority band |
| `_EMBEDDED_MISMATCH`, `_HARDWARE_MFG_MISMATCH`, `_INTEGRATION` | `scoring.py:93-116` | domain exclusions written against Elbit/Rafael JDs |
| `_CANDIDATE_LANGS`, `_KNOCKOUT_LANG_CANDIDATES` | `gates.py:114,135` | Go/Rust = knockout, Java = fine |
| `_FALSE_CLAIM_PATTERNS`, `_EXAMPLESAAS_SIG`, `_EXAMPLESTREAM_MARKER`, `_SEEKING_ALPHA_MARKER` | `gates.py:223-265` | **the owner's biography as ground truth** — a second user's *true* "MongoDB" or "Go" line is flagged as a lie |
| `SALARY_SOURCES`, `_NIS_FIGURE` | `gates.py:406,426` | Israeli salary sources, ₪ only |
| `Asia/Jerusalem` | `pipeline.py:167` | timezone |
| owner's Windows memory path | `checkpoint/SKILL.md:55,90` · `good-morning/SKILL.md:25` | `/checkpoint` and `/good-morning` fail at step 1 on any other machine |

`gates.py:217` already concedes the point in a comment: *"For the open-source template this should
become per-candidate config."*

A prior extraction exists (`../job-seeking-template`, one commit, **2026-07-06**) — it went stale
within days, is missing all of July's work, and **still carries the Israel geo-gate**. Evidence that
this needs doing once, properly, rather than by ad-hoc copy.

## Goals / non-goals

**Goals**
- One **private** repo both people commit to, sharing the platform and improving it together.
- **Neither person's job-search data is visible to the other**, by construction, not by discipline.
- Every career-shaped constant above becomes **profile configuration**; the logic adapts to it.
- Skills read the same profile, so prompts stop asserting one person's biography.
- user A's current behaviour is **bit-for-bit preserved** by expressing today's constants as his profile.
- CI proves a change helping one profile does not break the other.

**Non-goals**
- Public open-source release. Deferred; this design does not preclude it.
- Rewriting the skill *architecture* — orchestrator → filter → research → align → prep is sound and
  already person-neutral. Only its constants and exemplars change.
- Migrating user A's existing 469 personal files into the new repo (see Migration — they stay put).
- A UI, multi-tenant auth, or more than two users.

## Architecture — the decision that makes the rest easy

**Split platform from data. The repo holds only the platform; personal data lives outside it.**

```
job-seeking-platform/            ← the shared PRIVATE repo (both commit here)
  src/jobcore/                     engine, gates, PDF, ATS, pipeline ledger
  .claude/skills/                  skills (profile-aware, no biography)
  profiles/
    _template.json                 the schema, documented, copy-to-start
    _fixtures/                     2 synthetic profiles used ONLY by tests
  scripts/ · tests/ · docs/
  .env.example · ats_targets.example.json
  PROFILE_DIR                    ← env var, points OUTSIDE the repo

<your own private data dir>      ← NOT in this repo. Never committed here.
  profile.json                     your config
  cv.md                            your CV
  positions/ · job-tracking/       your pipeline, ledger, debriefs, prep
```

**Why not `profiles/<user-a>/` and `profiles/<user-b>/` inside one repo?** Because "private repo" is not
isolation — a shared repo means she reads his salary bands, rejections and self-scores, and he reads
hers, permanently and by default. That is exactly the disclosure this design exists to prevent. The
data directory is referenced by `PROFILE_DIR` and is **gitignored in this repo**; each person points
it at their own private repo or a local backed-up folder. user A's existing repo continues to hold his
data unchanged — nothing to migrate, nothing to strip.

**Consequence to accept:** the two of you cannot review each other's CVs through the repo. That is
the intended trade, and it costs nothing — you can still share a file directly when you want to.

## The profile schema

One file, `profile.json`, in `PROFILE_DIR`. JSON (not YAML) to match `pipeline.json` and
`ats_targets.json`, and to keep the dependency set unchanged.

```jsonc
{
  "schema_version": "1.0",
  "identity": {
    "name": "Dana Cohen",              // replaces YOUR_NAME; PDF filename derives from CV H1
    "cv_path": "cv.md",                // relative to PROFILE_DIR
    "email": "..."                     // never written into generated artifacts
  },
  "locale": {
    "timezone": "Europe/Berlin",       // replaces the hardcoded Asia/Jerusalem
    "currency": "EUR",                 // replaces ₪-only salary parsing
    "languages": ["en", "de"]          // drives interview-prep's query bank
  },
  "geo": {
    "mode": "preferred",               // "preferred" = rank, "required" = hard drop (today's behaviour)
    "preferred_locations": ["berlin", "potsdam", "remote"],
    "commute_extra": {}                // per-ATS-target widening, e.g. {"rafael": ["haifa"]}
  },
  "roles": {
    "target_titles":  ["frontend engineer", "senior frontend", "ui engineer"],
    "exclude_titles": ["qa", "product manager", "sales engineer"],
    "in_band_titles": ["senior", "staff"],
    "above_band_titles": ["director", "vp", "head of", "chief"],
    "require_leadership": false,       // a lead search: true. A frontend IC: false.
    "domain_exclusions": ["embedded", "firmware", "rtos", "hardware", "npi", "pcb"]
  },
  "skills": {
    "keywords": ["react", "typescript", "css", "accessibility", "vite"],
    "boost_keywords": ["react", "typescript"]   // the +2 bonus, today hardcoded to python|kafka|...
  },
  "facts": {                           // ← the gate ground-truth, per candidate
    "known_languages":   ["typescript", "javascript", "python"],
    "knockout_languages": ["go", "rust", "scala"],
    "employers": [
      { "name": "Acme",  "markers": ["design system", "monorepo"] }
    ],
    "never_claim": ["kubernetes", "kafka"],     // replaces _FALSE_CLAIM_PATTERNS
    "rank_or_credential": null                   // replaces the hardcoded "Captain"/IDF check
  },
  "salary": {
    "currency": "EUR", "open": 95000, "target": 88000, "floor": 80000, "period": "year",
    "sources": ["levels.fyi", "glassdoor.de"]
  }
}
```

**user A's profile is today's constants, verbatim** — `CENTER_ISRAEL` → `geo.preferred_locations`,
`geo.mode: "required"`, `require_leadership: true`, the current `CV_KEYWORDS`, `₪`/`Asia/Jerusalem`,
and his `facts` block. That is what makes the migration provably behaviour-preserving.

## How each layer consumes it

**`src/jobcore/profile.py` (new)** — the single loader.
- `load_profile(path=None) -> Profile` — resolves `PROFILE_DIR` env → `--profile` flag → `./profile.json`.
- `validate(profile) -> list[str]` — same contract as `pipeline.validate`; unknown keys, bad enums,
  missing required fields. Exits 1 from the CLI.
- **Calls `load_dotenv()` once, here** — fixing problem 2 at its root, so `.env` finally means something.
- CLI: `python -m src.jobcore.profile show | validate | --brief`.

**Scoring** — `is_relevant()` and `score()` take a `profile` argument (defaulted, so no call-site
churn). The regexes are **compiled from profile lists at load time**, not at import. `geo.mode`
decides drop-vs-rank, which is the single change that unblocks a non-Israeli user.

**Gates** — `_CANDIDATE_LANGS`, `_FALSE_CLAIM_PATTERNS`, the employer signatures and the rank check
all read `profile.facts`. A gate with an empty `facts` block must **skip and say so**, never
silently pass — the `cov=n/a` lesson: *a skipped gate is not a green gate.*

**Skills** — no skill hardcodes a fact or a path. Each one that needs context starts with:

> Run `uv run python -m src.jobcore.profile --brief` and use its output as the candidate profile.
> Do not assume any employer, language, location or seniority not present there.

`--brief` prints a compact, token-cheap summary designed to be pasted into a prompt. The two skills
with the hardcoded Windows path derive it instead from the repo slug under `~/.claude/projects/`.

## Privacy model — the part that is not optional

1. **`PROFILE_DIR` is gitignored in this repo**, and CI fails if any path under it becomes tracked.
2. **A pre-commit guard** (extending the existing `detect-secrets` hook) rejects a commit that adds a
   CV, a `positions/` folder, or an email address outside `profiles/_fixtures/`.
3. **Fixtures are synthetic.** `profiles/_fixtures/*.json` contain invented people. No real name,
   employer or salary in the shared repo, ever — even though it is private.
4. **The new repo starts with `git init`, not a fork or a clone.** A fork carries the history, and the
   history carries two named recruiters' email addresses. This is the reason the extraction cannot be a
   `git filter-branch` of the current repo.

## Collaboration: proving a change is safe for both

The real risk of a shared platform is a change that improves one person's results and quietly wrecks
the other's. Discipline will not catch that; a test will.

**Parametrize the scoring and gate suites over both fixture profiles.** A backend-leadership fixture
and a frontend-IC fixture, with an assertion that each scores its own roles highly and the other's
appropriately. Any future edit to a constant-turned-config must keep both green. This is the
mechanism that makes "we both work on the same repo" actually workable.

## Migration — phased so nothing breaks

| Phase | Work | Done when |
|---|---|---|
| 0 | `profile.py` + schema + `load_dotenv` + `_fixtures/` | `profile validate` passes; full suite still green |
| 1 | Scoring reads the profile; `user-a.json` reproduces today's constants | **existing scoring tests pass unchanged** — the proof of behaviour preservation |
| 2 | Gates read `profile.facts`; empty facts ⇒ explicit skip | gate tests pass for both fixtures |
| 3 | Skills de-personalized; memory path derived | `/good-morning` and `/checkpoint` run on a clean machine |
| 4 | Fresh `git init` repo; copy the 88 infra files + docs + `_template/`; **no history** | a clean-machine clone reaches a first ranked shortlist using only `SETUP.md` |
| 5 | Delete the stale `../job-seeking-template` | it can no longer be mistaken for current |

User A keeps using his existing repo throughout; it becomes his `PROFILE_DIR` once phase 4 lands.

## Testing

- `profile.validate`: unknown key, bad enum, missing required, empty-facts-skips-gate.
- **Behaviour preservation:** the existing `test_scoring.py` / `test_quality_gates.py` pass against
  `user-a.json` **without edits**. If they need edits, the migration changed behaviour and is wrong.
- **Second-profile sanity:** the frontend-IC fixture in Berlin gets a sensible ranking — a Berlin
  React role scores high, a Tel-Aviv backend-TL role does not. Today it scores 1 and is dropped.
- **Isolation:** a test asserts nothing under `PROFILE_DIR` is git-tracked, and that
  `profiles/_fixtures/` contains no real-looking email.
- Full gate (`bash scripts/verify_all.sh`) green before any phase is called done.

## Risks

- **Regex-from-config is slower and can be malformed.** Compile once at load; `validate` rejects a
  bad pattern with the offending key named, rather than throwing at scoring time.
- **Profile drift between the two users** — one adds a field, the other's file lacks it. Mitigated by
  `schema_version` and validation failing loudly rather than defaulting silently.
- **Effort is real: ~12–20h.** Roughly 4h strip/seed, 6h lift the constants, 3h de-personalize the
  skills, 3h tests + `SETUP.md`. The highest-leverage single hour is phase 0's `load_dotenv` +
  loader, because phases 1–3 then become data edits rather than code archaeology.
- **This design leaves user A's existing repo untouched**, which means the two recruiter emails stay in
  its history. If that repo is ever shared with anyone, it needs `git filter-repo` first — out of
  scope here, but do not lose the thread.
