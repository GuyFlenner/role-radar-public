---
name: gated-commit
description: Commit + push in role-radar with the full CI-gate contract and correct Git Bash syntax. Use when about to commit or push, so "green" is verified (not assumed) and no PowerShell here-string corrupts the commit.
---

# Gated Commit

Bakes in the two recurring lessons from `/insights` (2026-07-24): **(1) verify ALL gates before claiming green; (2) never use PowerShell syntax in the Bash tool.**

## Steps

1. **Verify green (the contract):** run
   ```bash
   bash scripts/verify_all.sh
   ```
   It must print `✅ GREEN`. If any blocking gate fails, **STOP and fix first** — never proceed on a partial run or a bare "looks clean."
2. **Branch check:** if on `main`, create a feature branch first (this repo's convention: push branch → PR → merge server-side; `gh` may be absent).
3. **Stage** only the intended files (`git add <paths>`), not `-A` blindly.
4. **Commit with a POSIX heredoc** — NEVER a PowerShell here-string (`@'…'@`):
   ```bash
   git commit -F - <<'EOF'
   type(scope): subject

   body

   Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
   EOF
   ```
5. **Push.** The pre-push hook re-runs the CI-mirror gates. If it blocks, fix the root cause — do **not** `--no-verify`.
6. **Report which gates ran** (paste the `verify_all.sh` summary) — never a bare "green."

## Escaped-defect rule

If a bug slipped past an existing gate: **fix + add a regression test** (must fail on the old code, pass on the fix) **+ harden the gate**. Not just the fix.

## Notes

- Local skill (net-new, not in `claude-skills.lock`) — the sync script won't touch it.
- See `CLAUDE.md` → "Shell & Git Conventions" and memory `feedback_shell_git_bash`, `feedback_verify_full_ci_gate_set`.
