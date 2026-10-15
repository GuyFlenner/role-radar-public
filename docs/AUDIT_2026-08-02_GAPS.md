# role-radar — extraction audit, 2026-08-02

Two independent LLM judges scored this repo against the original single-user repo (the single-user original)
on a shared rubric. This file records the verdict, what has been fixed, and what has not.

## Scores

| Dimension | Weight | original repo | role-radar |
|---|---|---|---|
| Generality (works for a second person) | 25% | 1.5 | 8.0 |
| Privacy (personal data cannot leak) | 25% | 3.0 | 9.5 |
| Gate integrity (a green gate means something) | 20% | 6.5 | 6.5 |
| Tests | 15% | 6.5 | 8.0 |
| Onboarding (clean machine, no memories) | 10% | 4.0 | 8.5 |
| Operational | 5% | 8.5 | 6.5 |
| **Weighted total** | | **4.2 / 10** | **8.1 → 8.25 / 10** |

Rescored after rounds 2 and 3 by a fresh judge: **8.25/10**. Generality 7, Privacy 9,
Gate integrity 9, Tests 8, Onboarding 8, Operational 9. Generality was the only dimension
below 8, and its cause was named precisely — the refactor stopped at the Python boundary — so
that became the locale gate below rather than a note.

the original single-user repo scores low *by design* — it is one person's workspace and was never trying to be
a platform. The comparison is only meaningful against role-radar's stated purpose.

## The pattern worth remembering

> Every previously-fixed defect was a **missing artifact, found by absence**. Every defect the
> judges still found was a **present artifact that lies**. The extraction validated existence
> and never coherence.

The tell was `check_parity.py`: the team correctly diagnosed "the copy loop's own success was
treated as proof", wrote the fix — and wired it into no gate, repeating the same mistake one
level up.

This repeated once more *during* the fixes. Commit `327e88d` fixed the RAG gate passing
vacuously; `b89e04a` had to fix the fact that `.gitignore` then kept the CI fixture profile out
of the commit entirely, so the gate reported SKIPPED on a fresh clone and the CI step passed
having asserted nothing. It scored 3/3 locally only because the untracked file was in the
working tree. **Verifying locally is not verifying what CI runs.** The clean-clone check is now
the habit: `git clone . /tmp/x && cd /tmp/x && <the exact CI command>`.

## Fixed (all verified, `verify_all.sh` green on 4/4 blocking gates)

| # | Defect | Commit |
|---|---|---|
| 1 | RAG goldens gate printed `PASS goldens 0/0` and exited 0. `EvalReport.passed` is `all([])` → True, and `run_corpus_gate` bypassed `run_gate`'s existing guard. Now a four-state contract. | `327e88d` |
| 2 | That gate was in **neither** automated path — no `ci.yml` step, and the `ci-rag-goldens` pre-push hook was dropped in extraction — while `verify_all.sh` claimed to mirror CI "EXACTLY". | `327e88d` |
| 3 | `csv_ingest` still hardcoded `"Referally Leadership*.csv"` + an inline `"Referally" in sender` test. A second user got **zero rows and no error**. | `327e88d` |
| 4 | `rag` and `csv` were read at runtime but absent from `_skeleton()`, the template, both fixtures and `validate()`. | `327e88d` |
| 5 | `console.force_utf8_output()` was extracted from `check.py` and its three tests were dropped in the move. | `327e88d` |
| 6 | `.gitignore` excluded the CI fixture profile → the CI gate was a no-op on a clean clone. | `b89e04a` |
| 7 | `.env.example` shipped 38 keys; code reads 13. The other 34 are owned by `profile.json` and were silently ignored. | `4414f4d` |
| 8 | `positions/_template/` never shipped — git cannot re-include inside an excluded **directory**, so the `!` negation was dead. | `4414f4d` |
| 9 | `_credential_violations` and `_employer_attribution_violations` returned `[]` — not SKIPPED — on a partially-configured `facts` block. | `1cb797d` |

## Round 2 — the skill layer, and a defect class the gates could not see

The first round fixed the deterministic half. Round 2 started from "what else is a present
artifact that lies" and found the prose layer had rotted invisibly, because **prose has no
test**.

| # | Defect | Severity | Commit |
|---|---|---|---|
| 10 | 12 of 14 CLIs never called `force_utf8_output()`. ASCII output garbled; **non-Latin output raised `UnicodeEncodeError`** — proven. For a geography-configurable platform that is the normal case for a non-English-market user. `job-radar` even documented a manual `PYTHONIOENCODING=utf-8` workaround for it. | MAJOR | `fd5bac6` |
| 11 | Skills instructed Claude to read **14 env vars no code had read since the profile refactor**, and to `cp` four templates from a directory that does not exist. | MAJOR | `077ba52` |
| 12 | `position-filter` re-implemented the deterministic core **in prose** — hand-parsing CSV, hand-scoring, hand-dropping stale rows. Two implementations of one rule, the prose one in the expensive lane. | MAJOR | `077ba52` |
| 13 | Locale defaults hardcoded in the prose layer (`job-radar`'s geo band, `--location "Israel"`, a ₪ salary figure) while the Python was profile-driven. | MAJOR | `1773686` |
| 14 | `CLAUDE.md` orphaned shipped tooling — 3 subagents, `usage.py`, `capped.sh`, the sync script, all documented nowhere. | MINOR | `1773686` |
| 15 | **Private-process and employer references in tracked file CONTENT.** `claude-skills.lock` disclosed a private legal process the owner is involved in; two `rag/` docstrings named the employer. (Describing it precisely here would reproduce the leak — see `df01c67`.) Every privacy test checked paths. | BLOCKER | `df01c67` |

`check_parity.py` is resolved differently from how the audit proposed: it takes a source-repo
path CI does not have, so it **cannot** be a gate. It is now documented as the one-off tool it
is. Bolting a checker into a place it cannot run is how you get another artifact that lies.

### New guardrails (the "escaped defect → fix + regression test + guardrail" rule)

- **`src/jobcore/skill_lint.py`** — blocking gate in all three enforcement points. Skill prose
  must reference config keys and repo paths that exist. Tuned hard for precision: the first
  draft produced 30 findings of which ~27 were false (`APPLIED` is a ledger enum, `8/10` is not
  a path), and *a noisy gate gets switched off, which is worse than no gate*.
- **CLI encoding meta-test** — discovers entry points and asserts each guards, so a 15th CLI
  cannot ship without it. Plus a test that the discovery itself finds ≥10 modules, since broken
  discovery would make the whole class vacuously pass.
- **Content-level privacy tests** — private-legal vocabulary and real-looking emails across
  every tracked text file, with a guard-the-guard test proving the match fires.

## Round 3 — the same bug, a third level down, self-inflicted

Both re-run judges stalled before scoring, but each surfaced one lead first. Both were real.

| # | Defect | Severity | How found |
|---|---|---|---|
| 16 | **The CI fixture's goldens asserted nothing.** They used `expect_source`, a key that has never existed in this schema. The constructor silently dropped it, so all three parsed to `must_retrieve=() top_source=None min_score=0.0` and passed unconditionally. `PASS rag-goldens goldens 3/3` was measuring nothing. | BLOCKER | judge lead, then reproduced |
| 17 | Three prompts read an "Active Positions table in `CLAUDE.md`" that does not exist — the pipeline ledger replaced it. Two of them live in `.claude/commands/`, which the new skill-lint **was not reading at all**. | MAJOR | judge lead, then reproduced |

Defect 16 is the same `all([])` bug at a third depth, and **it was introduced by the commit that
fixed the first two** — written into the very fixture whose purpose was to prove the gate works.
It passed a clean-clone check, because a vacuous pass is indistinguishable from a real one from
outside. The only thing that caught it was an adversarial reader asking "can this gate fail?"
rather than "does this gate pass?".

The invariant that kills the whole family: **a golden with no assertions is a configuration
error, not a pass**, enforced at both parse time (unknown keys raise) and score time
(assertion-free goldens fail). Verified all three ways: 3/3 PASS with real assertions, FAIL on a
golden pointing at a missing file, FAIL on an assertion-free golden.

Defect 17's lesson is about the gate rather than the bug: `skill_lint` globbed `*/SKILL.md` and
reported "13 skills" while 14 prompt files existed. **A gate with a blind spot reports on what it
looked at, not on what exists** — and it says PASS while doing so. It now covers
`.claude/commands/` and reports the true count.

## Round 4 — the rescore, and the last hardcoded market

| # | Defect | Severity | How found |
|---|---|---|---|
| 18 | The prompt layer still pinned the platform to one market: a commute band in `poll-jobs`, a salary benchmark mandated in one currency in `company-researcher`, one country as a default search location. The Python was profile-driven; the half a user touches was not. | MAJOR | rescoring judge |
| 19 | `SETUP.md:17` told a new user to `cd job-seeking-platform`. The repo is `role-radar`. Step 1 of onboarding, wrong. | MINOR | rescoring judge |

The guardrail is a **locale check in `skill_lint`** — no currency symbol or country/city name as
an *instruction*. It immediately found six more hardcodes that two manual passes had missed.

Lines marking themselves as illustrations ("e.g.", "example", "reference test") and fenced blocks
are exempt on purpose. The Exa A/B test that proved search-to-mirror beats fetching a walled URL
is measured evidence; the salary lesson about blended manager+team-lead averages needs its worked
figure to land. **The rule is about defaults, not examples** — scrubbing evidence to satisfy a
regex would be destroying methodology to pass a pattern match.

Checked and NOT a defect: "only 8 of 13 skills read `profile --brief`". The five that don't
(`architect`, `checkpoint`, `diagram-generator`, `diagram-layout-reviewer`, `gated-commit`) make
no candidate-specific judgment. 8/13 is correct.

## Open

Nothing verified-and-unfixed at the time of writing.

The single most useful habit this audit produced: ask **"can this gate fail?"**, not "does this
gate pass?". Three of the worst defects here — `goldens 0/0`, the untracked fixture, and the
assertion-free goldens — all presented as a green gate, and only the first question found them.

## What to read, in order

1. This file — the open list above is the work queue.
2. `src/jobcore/rag/cli.py` → `run_corpus_gate`'s four-state table. It is the reference for how
   a gate in this repo should distinguish *not configured* from *cannot run* from *clean*.
3. `src/jobcore/tests/test_profile.py` → `SCAFFOLDING_ALLOWLIST` and its two guard tests, for
   the pattern of guarding an exception rather than only a rule.
