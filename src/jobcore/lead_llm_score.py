"""Automated LLM-quality lead scoring for the Gmail poller — OpenRouter `:free` tier only.

This is the middle ground `docs/superpowers/specs/2026-08-20-headhunter-email-intake-design.md`
Architecture Decision (2) didn't fully explore: computing an LLM-quality (not just keyword)
fit read automatically, inside the unattended GitHub Action, without violating the "no paid
API key as a durable secret" constraint that rejected `LLM_PROVIDER=anthropic`/`openai` there.

Why this is safe where the rejected alternatives weren't: an OpenRouter API key restricted to
a `:free`-suffixed model is **structurally incapable of spending money** — OpenRouter's free
tier is rate-limited, not metered, so there is no equivalent of "a scheduled job that silently
accrues API spend" (the exact concern that ruled out Anthropic/OpenAI there). The model is
therefore hardcoded here, not read from `OPENAI_MODEL`/`LLM_FALLBACK_CHAIN` — this call must
never be able to drift onto a paid model just because some other env var changed. This is one
new durable secret (`OPENROUTER_API_KEY`, mapped into `OPENAI_API_KEY` per `jobcore.llm`'s
provider-alias convention), same "durable secret exists" trade-off the design doc already
named for the rejected options, but the spend-risk half of that trade-off doesn't apply here.

This never bypasses `lead_approval` (see `batch_guard.NEVER_SKIPPABLE`) — it only pre-fills
`triage.llm_score`/`llm_explanation`, the same fields the interactive `email-lead-triage` skill
computes by hand today. A human still approves before anything reaches `/job-apply`.

CLI (for manual/local testing against a real lead + the master CV):
    uv run python -m src.jobcore.lead_llm_score "<lead text>" job-tracking/cv.md
(reads OPENROUTER_API_KEY from the environment)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .console import force_utf8_output
from .llm import complete_structured

#: Hardcoded, not sourced from any general LLM_* env var -- see module docstring. Any model
#: passed here MUST carry the `:free` suffix; `score_lead` asserts this on every call as a
#: structural guard, not just a comment, mirroring this repo's own "a rule enforced only by
#: prose is the same defect as a check that never fires" doctrine (batch_guard.py).
#:
#: OpenRouter's free-tier catalog rotates -- the original pick (qwen/qwen3-coder:free) was
#: pulled from free availability and started 404ing live (2026-08-26, both the Gmail and
#: Telegram pollers). Confirmed the current catalog via the live API
#: (`curl https://openrouter.ai/api/v1/models`), not from training-data memory, since a
#: WebFetch summary of the same page hallucinated a materially different, wrong model list.
#: If this model is ever pulled too, re-check the live catalog the same way -- do not guess.
MODEL = "google/gemma-4-31b-it:free"

_SYSTEM = (
    "You are a blunt technical recruiter screening an inbound job lead against a candidate's "
    "CV. Be concise; no preamble."
)

_SCHEMA = {
    "type": "object",
    "required": ["score", "explanation"],
    "properties": {
        "score": {"type": "integer"},
        "explanation": {"type": "string"},
    },
}


def build_prompt(lead_text: str, cv_text: str) -> str:
    """Pure prompt assembly (unit-testable without a live LLM)."""
    return (
        f"## CANDIDATE CV\n{cv_text[:6000]}\n\n"
        f"## INBOUND LEAD (captured email or message — may be a recruiter pitch, a JD, or "
        f"an unrelated message; judge honestly, including a low score if it isn't a real "
        f"lead)\n{lead_text[:4000]}\n\n"
        "Score fit 0-10 (score) and give a one-sentence explanation (explanation) naming the "
        "single strongest match or the clearest mismatch."
    )


def score_lead(lead_text: str, cv_path: Path, api_key: str) -> dict:
    """One structured OpenRouter `:free` completion -> ``{"score": int, "explanation": str}``.

    ``api_key`` is taken explicitly rather than read from ``OPENAI_API_KEY`` by
    `jobcore.llm`'s own default -- this repo's real secret is named
    ``OPENROUTER_API_KEY`` (an accurate name for what it actually is), and the caller is
    responsible for reading that and passing it in here, not this module reaching for a
    same-shaped but differently-named env var behind the scenes.

    Raises RuntimeError on any failure (missing/invalid key, network error, schema mismatch
    after retry) — callers must catch this. Best-effort by contract: a failure here must never
    block lead capture, the same shape as `gmail_poll.py`'s Telegram notification.
    """
    if not MODEL.endswith(":free"):
        raise RuntimeError(f"lead_llm_score.MODEL must be an OpenRouter :free model, got {MODEL!r}")
    cv_text = cv_path.read_text(encoding="utf-8")
    prompt = build_prompt(lead_text, cv_text)
    return complete_structured(_SYSTEM, prompt, _SCHEMA, provider="openrouter", model=MODEL, api_key=api_key)


def main() -> int:
    force_utf8_output()
    if len(sys.argv) != 3:
        print("usage: python -m src.jobcore.lead_llm_score '<lead text>' <cv_path>", file=sys.stderr)
        return 1
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("lead_llm_score: OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    lead_text, cv_path = sys.argv[1], Path(sys.argv[2])
    try:
        result = score_lead(lead_text, cv_path, api_key)
    except RuntimeError as exc:
        print(f"lead_llm_score: {exc}", file=sys.stderr)
        return 1
    print(f"Score: {result['score']}/10")
    print(f"Explanation: {result['explanation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
