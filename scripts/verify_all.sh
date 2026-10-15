#!/usr/bin/env bash
# verify_all.sh — the single "are we green?" contract for role-radar.
#
# Mirrors .github/workflows/ci.yml's blocking `test` job step-for-step (ruff check, ruff format
# --check, pytest, rag goldens), so a clean run here
# means CI will pass for lint/format/tests. NEVER report "green" without a clean run of THIS
# script — a partial gate run is not a green run (see CLAUDE.md → Shell & Git Conventions).
#
# Usage:  bash scripts/verify_all.sh
# Exit:   0 only if every BLOCKING gate passed. Non-zero if any blocking gate failed.
#         There are no advisory gates any more: mypy became blocking on 2026-08-02, and the
#         `run_advisory` helper was removed with it rather than left sitting unused.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

summary=()
failed=()

run_gate() {
  local name="$1"; shift
  echo "─── $name ───"
  if "$@"; then
    summary+=("PASS  $name")
  else
    summary+=("FAIL  $name")
    failed+=("$name")
  fi
  echo
}


# BLOCKING — the exact ci.yml `test` job steps
run_gate     "ruff check ."          uv run ruff check .
run_gate     "ruff format --check ." uv run ruff format --check .
run_gate     "pytest"                uv run python -m pytest -q
# Publish guard. BLOCKING here, not only at push time: "is anything personal tracked?" is a
# question you want answered on every commit, not once when you finally run setup-github.sh.
run_gate     "publish check"         uv run python -m src.jobcore.publish_check

# Skill-prompt lint. BLOCKING. A SKILL.md is executable — Claude reads it and does what it says —
# but nothing compiles it, so it rotted invisibly: 14 `.env` keys no code had read since the
# profile refactor, and a `cp` from a directory that does not exist. Prose has no test unless you
# write one.
# Diagram UX gate. BLOCKING. drawio_lint.py was 900 lines with 54 tests wired into NOTHING, so a
# diagram could ship with arrows through boxes and text invisible on a dark canvas while every
# gate said green. Zero diagrams reports SKIPPED (exit 0) - never PASS.
run_gate     "drawio diagrams"       uv run python -m src.jobcore.diagram_gate

run_gate     "skill lint"            uv run python -m src.jobcore.skill_lint

# RAG golden-question gate. BLOCKING, and deliberately so: it builds its index in
# memory from the working tree on every run, so it can never pass off a stale index,
# and it exits 1 when a golden regresses. Runs dependency-free (lexical ranking) —
# the `rag` extra only changes the backend, never whether the gate runs.
# Same invocation as ci.yml and the pre-push hook: the fixture corpus, so the gate scores real
# goldens instead of reporting SKIPPED against a developer profile that may not use the lane.
run_gate     "rag goldens"           env PROFILE_DIR=profiles/_fixtures/rag_demo uv run python -m src.jobcore.rag --root profiles/_fixtures/rag_demo eval

# BLOCKING as of 2026-08-02. It was advisory with 11 standing errors, which made it a gate that
# did not gate — the same defect class as every other one this repo has hunted. Two of those
# errors were real (`scorer: object` passed where a Callable was required; a repo-wide
# `set[tuple[str, str]]` annotation that had been false since `job_key` started emitting
# 3-tuples). All 11 are fixed; keeping it advisory now would only let the 12th in.
run_gate     "mypy src/jobcore"      uv run mypy src/jobcore

echo "════════════ verify_all summary ════════════"
for line in "${summary[@]}"; do echo "  $line"; done
echo "═════════════════════════════════════════════"

if [ "${#failed[@]}" -ne 0 ]; then
  echo "❌ NOT GREEN — ${#failed[@]} blocking gate(s) failed: ${failed[*]}"
  exit 1
fi
echo "✅ GREEN — all blocking gates passed."
