# `positions/` lives in $PROFILE_DIR, not here

This directory is a **seed**, kept so a fresh clone shows the expected shape. Your real positions
live in `$PROFILE_DIR/positions/` and are gitignored in this repo — see `SETUP.md`.

Per-position layout:

```
positions/{company-slug}/
  JD_DESCRIPTION.md     the verbatim job description + apply URL + liveness
  COMPANY_RESEARCH.md   what the company does, stack, who hires
  CV_TAILORED.md        the tailored CV (source for the PDF)
  gate_terms.txt        ATS must-have terms — REQUIRED, or coverage reports cov=n/a (SKIPPED)
  STATUS.md             stage + outcome + next action
```

⚠️ **`gate_terms.txt` is not optional.** Without it `src.jobcore.check` reports `cov=n/a`, which
means the ATS coverage gate was **skipped, not passed**. Curate it to real must-haves only —
padding it with "advantage" items makes 100% unachievable and pressures the CV toward claims the
candidate cannot back.
