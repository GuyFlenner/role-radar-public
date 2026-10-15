"""Headless fit-note — demonstrates the vendor-lock escape hatch end to end.

Given a JD file and a CV file, this runs the deterministic gates (jobcore.gates)
and then asks the configured LLM (jobcore.llm — Ollama by default, free/local)
for a short fit assessment. It needs NO Claude Code: `src/jobcore/` + a local
Ollama model is a complete, provider-agnostic path for the judgment work.

  uv run python -m src.jobcore.fit_note positions/<slug>/JD_DESCRIPTION.md \
                                        positions/<slug>/CV_TAILORED.md

Provider is whatever LLM_PROVIDER selects (default ollama). See jobcore/llm.py.
"""

import sys
from pathlib import Path

from .console import force_utf8_output
from .gates import find_knockout_gaps, find_repeated_clauses
from .llm import complete_structured_with_fallback

_SYSTEM = (
    "You are a blunt technical recruiter. Given a job description and a tailored CV, "
    "assess the fit. Be concise; no preamble."
)

# The structured-output demo this module exists for: complete_structured_with_fallback
# guarantees this exact shape back, or raises -- no more hoping the model followed a
# free-text "(1)/(2)/(3)" instruction correctly (it previously just returned raw text,
# unparsed and unchecked; see docs/superpowers/specs/2026-08-25-prompt-chain-pattern-assessment.md).
_ASSESSMENT_SCHEMA = {
    "type": "object",
    "required": ["score", "strongest_match", "gap"],
    "properties": {
        "score": {"type": "integer"},
        "strongest_match": {"type": "string"},
        "gap": {"type": "string"},
    },
}


def build_prompt(jd_text: str, cv_text: str) -> str:
    """Pure prompt assembly (unit-testable without a live LLM)."""
    return (
        f"## JOB DESCRIPTION\n{jd_text[:4000]}\n\n"
        f"## TAILORED CV\n{cv_text[:6000]}\n\n"
        "Give the fit score, the strongest match, and the one gap."
    )


def deterministic_checks(jd_text: str, cv_text: str) -> str:
    """The no-LLM half — knockout + anti-drift gates. Always available offline."""
    knockouts = find_knockout_gaps(jd_text, cv_text)
    drift = [c for c, _ in find_repeated_clauses(cv_text)]
    return (
        "KNOCKOUTS: " + (", ".join(knockouts) if knockouts else "none") + "\n"
        "DRIFT: " + (", ".join(drift[:3]) if drift else "none")
    )


def llm_assessment(jd_text: str, cv_text: str) -> dict:
    """The LLM half — provider-agnostic (Ollama by default), schema-validated.

    Returns {"score": int, "strongest_match": str, "gap": str}, or raises
    RuntimeError — never an unparsed blob the caller has to hope is well-formed.
    `complete_structured_with_fallback` already handles LLM_FALLBACK_CHAIN
    internally (falls back to the single configured provider when unset), so this
    function no longer needs to branch on it itself.
    """
    prompt = build_prompt(jd_text, cv_text)
    parsed, meta = complete_structured_with_fallback(_SYSTEM, prompt, _ASSESSMENT_SCHEMA)
    label = f"{meta['provider']}:{meta['model']}" if meta.get("model") else meta["provider"]
    parsed["_answered_by"] = f"hop {meta['hop']}: {label}"
    return parsed


def format_assessment(assessment: dict) -> str:
    """Render the structured assessment for CLI display."""
    return (
        f"Score: {assessment['score']}/10\n"
        f"Strongest match: {assessment['strongest_match']}\n"
        f"Gap to address verbally: {assessment['gap']}\n"
        f"[answered by {assessment['_answered_by']}]"
    )


def main() -> int:
    force_utf8_output()
    if len(sys.argv) < 3:
        print("Usage: python -m src.jobcore.fit_note <JD.md> <CV.md>", file=sys.stderr)
        return 2
    jd = Path(sys.argv[1]).read_text(encoding="utf-8")
    cv = Path(sys.argv[2]).read_text(encoding="utf-8")

    # Deterministic half FIRST — always prints, even if the LLM is unreachable.
    print(deterministic_checks(jd, cv))
    try:
        print("\n--- LLM fit assessment ---\n" + format_assessment(llm_assessment(jd, cv)))
    except RuntimeError as e:
        print(f"\n[fit_note] LLM step unavailable ({e}) — deterministic checks above still valid.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
